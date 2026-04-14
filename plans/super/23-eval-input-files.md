# Super Plan — GH-23: Input Files Support on Eval Specs

## Meta

- **Ticket:** [clauditor#23](https://github.com/wjduenow/clauditor/issues/23)
- **Title:** Input files support on eval specs
- **Branch:** `feature/23-eval-input-files`
- **Phase:** devolved
- **Sessions:** 1
- **Last session:** 2026-04-13

---

## Ticket Summary

The [agentskills.io spec](https://agentskills.io/skill-creation/evaluating-skills) allows test cases to declare `files: ["evals/files/sales_2025.csv"]` — input files the skill operates on. Clauditor's `EvalSpec` only has `test_args: str`; there is no formal way to declare input files. This blocks evaluating file-transforming skills (data cleaners, PDF extractors, code refactorers) and forces users to hand-place files in cwd before `clauditor grade`, with no cleanup or isolation guarantees.

**Done when:** an eval spec with `"input_files": ["sales_2025.csv"]` runs successfully and the skill sees `sales_2025.csv` in its working directory.

---

## Discovery Findings

### Codebase landscape (from Codebase Scout)

| Area | Location | Relevant today |
| --- | --- | --- |
| `EvalSpec` schema | `src/clauditor/schemas.py:108-220` | JSON loader via `EvalSpec.from_file(path)`. No input-file field. No relative-path context — receives only `Path`. |
| Spec composition | `src/clauditor/spec.py:41-65` | `SkillSpec.from_file()` auto-discovers sibling `<skill>.eval.json`. Spec file path *is* available here for relative resolution. |
| Runner invocation | `src/clauditor/runner.py:180-193` | `subprocess.Popen([claude_bin, "-p", prompt, ...])` with `cwd=str(self.project_dir)`. `project_dir` defaults to `Path.cwd()`. No scratch-dir or staging layer. |
| Iteration workspace | `src/clauditor/workspace.py:141-239` | `allocate_iteration()` returns an `IterationWorkspace` with `tmp_path` (staging) and `final_path`; `finalize()` atomic-renames, `abort()` removes tmp. Per-run subdirs live at `iteration-N/<skill>/run-K/`. |
| Repo-root detection | `src/clauditor/paths.py:20-56` | `resolve_clauditor_dir()` walks up for `.git`/`.claude` marker. |
| CLI `grade` entry | `src/clauditor/cli.py:153-380` | Allocates workspace early (`_cmd_grade_with_workspace`), calls `spec.run()` at ~line 311. Natural place to stage input files is *after* workspace allocation and *before* `spec.run()`. |
| Pytest plugin | `src/clauditor/pytest_plugin.py:85-185` | Exposes `clauditor_runner`, `clauditor_spec`, `clauditor_grader`, `clauditor_triggers`, `clauditor_capture` factory fixtures. No `clauditor_input_files` today. |
| Captured-output mode | `tests/eval/captured/` | Directory pattern documented (`<skill>.txt`) but no files yet; `--output <file>` CLI flag bypasses runner entirely (`cli.py:307`). |
| Example eval spec | `examples/.claude/commands/example-skill.eval.json` | Canonical schema shape; no input_files today. |

### Relevant prior decisions (from plan #22, merged in PR #31)

- **DEC-003** — Workspace layout: `.clauditor/iteration-N/<skill>/run-K/` with aggregated `grading.json`+`timing.json` at the skill level.
- **DEC-011** — Single-run uses `run-0/` for structural consistency with variance mode.
- **DEC-012** — Atomic writes: tmp+rename. All staging lives under `.clauditor/iteration-N-tmp/<skill>/`, finalized by `os.rename()`.
- **DEC-014** — `--force` semantics: clean-slate `rmtree` before rewriting an iteration.
- **DEC-009** — Repo-root resolution: walk up from CWD for `.git`/`.claude` markers; `$HOME/.claude` deliberately excluded.

### Rules constraints (from Convention Checker)

- **No `.claude/rules/` directory, no `workflow-project.md`** — project is pre-production, no backwards-compatibility or versioning constraints.
- **Quality gate convention** (from plan #22): 4× code reviewer passes + CodeRabbit + ruff + pytest ≥80% coverage.
- **Test conventions** (CLAUDE.md): class-based tests, `asyncio_mode = "strict"`, `importlib.reload()` for plugin-loaded modules, `tmp_path` over raw `tempfile`.

### Ambiguities / open questions

1. **Staging location** — inside the iteration workspace (finalized alongside `run-K/output.txt`), or in a throwaway tmpdir outside the workspace?
2. **Persistence after finalize** — after a successful run, do we keep a copy of the input files inside `iteration-N/<skill>/` (so a re-grade can re-run the exact same inputs), or delete them on success?
3. **CWD vs. arg interpolation** — does the skill see files by name in its CWD (`sales_2025.csv`), or do we rewrite `test_args` with absolute paths?
4. **Mutation semantics** — if the skill modifies an input file (data cleaner, code refactorer), is the modified file captured as output? Related to `output_files` glob.
5. **Subdirectory structure** — if `input_files: ["data/sales.csv", "data/refunds.csv"]`, are relative subdirs preserved in staging?
6. **Captured-output mode interaction** — `clauditor grade --output captured.txt` bypasses the runner entirely. Does `input_files` validation still fire? (Probably no-op; files aren't staged since no run happens.)
7. **Pytest plugin surface** — transparent inside `clauditor_spec` factory, or a new `clauditor_input_files` fixture for explicit staging?
8. **Missing-file behavior at spec load** — hard error (abort spec load), or defer to run-time?
9. **Variance runs** — do all N runs share the same staged input files (single copy), or does each `run-K/` get a fresh copy in case the skill mutates them?
10. **`--keep-workspace` flag** — the ticket mentions it as a cleanup override, but iteration workspaces are already persistent post-merge of PR #31. Is the flag still needed, or does it collapse into existing iteration-persistence semantics?

---

## Proposed scope (for refinement)

- Add `EvalSpec.input_files: list[str]` (default `[]`).
- `EvalSpec.from_file(path)` becomes `EvalSpec.from_file(path)` with internal resolution: input_files paths resolved relative to `path.parent`, validated to exist at load time, and stored as absolute paths on the spec.
- `SkillSpec.run()` stages input files into the iteration workspace's per-run scratch area before invoking the runner.
- `SkillRunner` CWD is set to the staging dir so the skill sees files by plain name.
- Staged files are copied (not symlinked) so the skill can mutate them freely.
- Pytest plugin: transparent — `clauditor_spec` factory handles staging automatically.
- CLI: no new flag required (question 10 above to confirm).
- Captured-output mode: `--output <file>` bypasses runner → no staging needed; validation still fires at spec load.

---

## Scoping questions

**Q1 — Staging location & persistence after finalize.** Where do staged input files live on disk and what happens after a successful run?

- **A.** Stage into `iteration-N-tmp/<skill>/run-K/inputs/`. On finalize, files persist inside `iteration-N/<skill>/run-K/inputs/` alongside `output.txt`. Pro: re-grade and post-mortem can see exactly what the skill saw. Con: iterations get heavier if inputs are large.
- **B.** Stage into a throwaway `tempfile.mkdtemp()` dir outside `.clauditor/`. Cleaned up on success. Pro: keeps `.clauditor/` small. Con: can't re-run the exact inputs later.
- **C.** Stage into `iteration-N-tmp/<skill>/inputs/` (skill-level, shared across runs). On finalize, persisted at `iteration-N/<skill>/inputs/`. Pro: one copy shared across variance runs. Con: mutation in run-0 affects run-1.
- **D.** Hybrid — stage per-run (like A) but only persist if user passes `--keep-inputs`. Default is cleanup-on-finalize.

**Q2 — CWD vs. absolute-path arg rewriting.** How does the skill reference the staged files?

- **A.** CWD is set to the staging dir. The skill sees `sales_2025.csv` as a plain filename. `test_args` stays as-is. (Matches agentskills.io spec wording: "files the skill operates on".)
- **B.** CWD stays at repo root (current behavior). Clauditor rewrites `test_args` by substituting `{{input_files[0]}}` tokens with absolute paths. Pro: explicit, no CWD magic. Con: requires new templating and breaks `test_args` opacity.
- **C.** CWD is set to the staging dir *and* `{{input_files[0]}}` tokens are supported for cases where the skill expects absolute paths. Hybrid.

**Q3 — Mutation & output capture.** If the skill mutates an input file (e.g. a CSV cleaner rewrites `sales.csv`), how is the result captured?

- **A.** No special handling. If the user wants to capture the mutated file, they declare it in `output_files: ["sales.csv"]` — `output_files` already globs against CWD. The staging dir being CWD makes this work naturally.
- **B.** Auto-snapshot: clauditor diffs each input file before/after and records `input_files_mutated: [...]` in `grading.json`.
- **C.** Files are staged read-only (`chmod a-w`); any mutation attempt is a spec failure.

**Q4 — Missing-file behavior at spec load.** A spec declares `input_files: ["missing.csv"]` and the file doesn't exist relative to the spec dir.

- **A.** Hard error at `EvalSpec.from_file()` — matches existing fail-fast pattern (`FORMAT_REGISTRY`, regex compile).
- **B.** Warn at load, hard error at run time (when staging would happen).
- **C.** Silently skip missing files, stage whatever is present.

**Q5 — Variance runs: shared or per-run staging?** With `variance.n_runs=5` and a mutating skill:

- **A.** Per-run fresh copy: each `run-K/inputs/` gets its own copy of the declared input files. Guarantees run independence. (Pairs with Q1-A.)
- **B.** Shared copy at skill level: all runs share one `inputs/` dir. Faster, less disk, but mutation in run-0 contaminates run-1..run-N. (Pairs with Q1-C.)
- **C.** Shared copy, but snapshot+restore between runs (re-copy from the spec's source dir before each run). Middle ground.

**Q6 — Pytest plugin surface.** How does `input_files` work inside `pytest` tests that call `clauditor_spec` / `clauditor_runner` directly?

- **A.** Transparent: the `clauditor_spec` factory stages files into `tmp_path` automatically when the loaded eval spec has `input_files`. Tests get no new fixture.
- **B.** Explicit: add `clauditor_input_files(spec)` factory that returns the staging dir path; tests wire it into `runner.project_dir` manually.
- **C.** Both — transparent default, but `clauditor_input_files` exposed for tests that want to pre-seed mutated input files.

---

## Architecture Review

### Ratings

| Area | Rating | Headline |
| --- | --- | --- |
| Security — source path traversal | **blocker** | `input_files: ["../../../etc/passwd"]` resolves cleanly via `Path.resolve()`. Must enforce containment. |
| Security — destination path traversal | **blocker** | Subdir preservation (`data/../../escape.csv`) could write outside the staging root. |
| Security — absolute paths | concern | `/etc/passwd` as an entry is technically valid today. Least-surprise: reject. |
| Security — symlinks | concern | `shutil.copy2` follows symlinks by default. Copy content (not the symlink) and accept the small supply-chain risk, OR `follow_symlinks=False`. |
| Security — file size / DoS | concern | No size cap. Acceptable for pre-production / author-trusted specs. Document. |
| Security — trust model | pass | README already frames specs as developer-authored; plan #13 echoes it. Add one-line trust note. |
| Data model — field placement | pass | Insert `input_files: list[str]` after `test_args`, mirrors `output_file`/`output_files` naming. |
| Data model — storage type | pass | Keep as `list[str]` (not `list[Path]`) for JSON round-trip parity with `output_files`. |
| Data model — path resolution timing | concern | Resolve + validate at **load time** in `EvalSpec.from_file()`, diverging intentionally from `output_files` (which stays CWD-relative, resolved at run time). Needs a DEC to document. |
| Data model — backward compat | pass | `history.py` v3 doesn't embed specs; `data.get("input_files", [])` default makes old specs load cleanly. |
| API — `output_files` glob collision | concern | If CWD is staging dir with inputs pre-copied, `output_files: ["*.csv"]` can match un-mutated inputs. Needs docs warning + ideally spec-load guard. |
| API — field name bikeshed | pass | `input_files` wins (parallel with `output_file`/`output_files`). |
| API — pytest fixture surface | pass | Transparent staging inside `clauditor_spec` factory; uses `tmp_path`. |
| Observability — logging | concern | Project uses `print()` (no logger). Staging should emit one stdout line per run matching `cli.py:62` informational style. |
| Observability — error messages | pass | Follow existing `ClassName(field=X): problem. solution-hint.` pattern (schemas.py:32-46). |
| Testing — file organization | pass | `TestEvalSpecInputFiles` in `test_schemas.py`, `TestStageInputs` in `test_workspace.py`, fixture test in `test_pytest_plugin.py`. Class-based, `tmp_path`-based. |
| Testing — coverage gate | concern | ~16-18 test cases needed to stay above 80%. Enumerated below. |
| Performance | pass | File copies are O(N·M) for N runs × M files. Acceptable; same disk cost as per-run output capture. |

### Key findings

**Blocker 1 — source path traversal (security).** `Path("/specdir") / "../../../etc/passwd"` resolved via `Path.resolve()` yields `/etc/passwd` — no error, full escape. Fix: at load time, compute `resolved = (spec_dir / entry).resolve()` and assert `resolved.is_relative_to(spec_dir)` (Python 3.9+). Anything failing that check is a `ValueError` at load. Same pattern clauditor uses for skill-name validation (workspace.py:32-61 regex-only tokens).

**Blocker 2 — destination path traversal (security).** If we preserve subdir structure (`input_files: ["data/sales.csv"]` → `run-K/inputs/data/sales.csv`), a malicious entry `data/../../escape.csv` could mkdir outside the staging root. Fix: after source-side containment check passes, derive the destination path using only `entry.name` (flatten) OR validate each path component against `^[A-Za-z0-9_.-]+$` (the same regex workspace uses for skill names). Recommendation: **flatten** — simpler, no collision surface, unless the user genuinely needs subdir structure.

**Concern 1 — absolute paths.** `input_files: ["/etc/passwd"]`. `Path.resolve()` is identity on absolutes. Cleanest policy: **reject `.is_absolute()` at load time**, because (a) absolute paths would bypass source containment, (b) they are non-portable across checkouts, (c) easy to add later if someone needs repo-absolute paths with an explicit `root:` field.

**Concern 2 — symlinks.** If `inputs/sales.csv` is a symlink to `~/.ssh/id_rsa`, `shutil.copy2` happily copies the private key. Two options: (a) `follow_symlinks=False` — copies the symlink itself, skill sees a broken link (the target isn't staged); (b) resolve the symlink, check that the *real* target is also inside the spec dir. Recommendation: **(b)** — call `resolved.resolve(strict=True)` and apply the same containment check. Matches user intent (the author put a symlink there on purpose, so the target should be colocated).

**Concern 3 — `output_files` glob collision (API).** With CWD=staging dir and `output_files: ["*.csv"]`, un-mutated inputs match the glob. Fix: (a) docs warning in README and example; (b) optional spec-load guard that raises if any `output_files` glob matches an `input_files` entry name. The guard is cheap — do it.

**Concern 4 — resolution-timing divergence.** `output_files` stays relative-to-CWD (resolved at run time against `runner.project_dir`). `input_files` resolves at load time against the spec file's parent. This is a deliberate split: outputs are runtime artifacts, inputs are pre-existing static assets. Needs a DEC entry so future readers aren't confused.

**Concern 5 — file size DoS.** No cap today. Eval specs are author-controlled; acceptable. Document in README ("input files are copied without size limits") and leave a `max_input_bytes` field as future work. Not a blocker for pre-production.

**Concern 6 — staging logging.** Follow cli.py:62 pattern: one info line per run, `Staged 3 input file(s) into run-0/inputs/`. Written to stdout, no prefix. Error path follows schemas.py:32-46 style: `EvalSpec(skill_name='foo'): input_files[1]='missing.csv' not found under {spec_dir}`.

**Concern 7 — test coverage.** 16-18 test cases enumerated in the testing review, broken down by module: 3 in `test_schemas.py` (field parse, defaults, to_dict round-trip), 4 in `test_spec.py` (load-time validation: missing / absolute / traversal / happy), 5 in `test_workspace.py` (stage_inputs helper), 2 in `test_runner.py` (CWD swap), 2 in `test_cli.py` (grade integration + variance), 2 in `test_pytest_plugin.py` (transparent fixture staging + captured-output bypass).

---

## Refinement Log

### Decisions

- **DEC-001 — Schema field.** Add `input_files: list[str] = field(default_factory=list)` to `EvalSpec` immediately after `test_args`. Stored as `list[str]` (absolute path strings post-resolve), not `list[Path]`, for JSON round-trip parity with `output_files`. **Why:** matches `output_file`/`output_files` naming and serialization style; keeps `to_dict()` trivial. **How to apply:** `schemas.py:122`.

- **DEC-002 — Load-time resolution.** `EvalSpec.from_file(path)` resolves each `input_files` entry against `path.parent`, validates existence, and stores the absolute path string. Diverges intentionally from `output_files` (which stays CWD-relative, resolved at run time) because inputs are pre-existing static assets while outputs are runtime artifacts. **Why:** fail-fast UX — typos surface at spec load, not mid-grade. **How to apply:** `schemas.py:EvalSpec.from_file`.

- **DEC-003 — Source containment (B1).** Each resolved entry must satisfy `resolved.is_relative_to(spec_dir)`. Violations raise `ValueError` with the `EvalSpec(skill_name=…): input_files[i]=… escapes spec dir` format. **Why:** prevents `../../../etc/passwd` escape. **How to apply:** part of `from_file` validation; test in `test_schemas.py` with `TestEvalSpecInputFiles::test_path_traversal_rejected`.

- **DEC-004 — Reject absolute paths (C1, R2-A).** Entries where `Path(entry).is_absolute()` raise `ValueError` at load. **Why:** portable specs, no surprise staging of system files. **How to apply:** checked before `resolve()` in `from_file`.

- **DEC-005 — Symlink policy (C2, R3-A).** Resolve each entry with `resolve(strict=True)`, then apply the containment check to the real target. If the symlink target lives outside the spec dir, raise `ValueError`. Staged content is the target's bytes. **Why:** intentional symlinks are fine; escape via symlink is not. **How to apply:** reuse the containment check from DEC-003.

- **DEC-006 — Flatten subdirs (B2, R1-A).** `input_files: ["data/sales.csv"]` is staged as `run-K/inputs/sales.csv` — the destination uses `Path(entry).name` only. Duplicate basenames across entries raise `ValueError` at spec load. **Why:** eliminates destination-traversal surface entirely; simpler mental model. **How to apply:** basename-collision check lives next to DEC-003/004.

- **DEC-007 — Staging layout.** Files land in `.clauditor/iteration-N-tmp/<skill>/run-K/inputs/<basename>`. On `workspace.finalize()` the atomic rename persists them at `iteration-N/<skill>/run-K/inputs/<basename>`. **Why:** matches DEC-003/012 from plan #22 (tmp+rename, per-run artifacts); re-grade can reuse the exact input bytes. **How to apply:** new `workspace.stage_inputs(run_tmp_dir, abs_paths)` helper.

- **DEC-008 — CWD = staging dir (Q2-A).** `SkillRunner` is invoked with `cwd=<run-K/inputs absolute path>`. `test_args` is never rewritten. **Why:** matches agentskills.io wording ("files the skill operates on"); zero templating surface. **How to apply:** `SkillSpec.run()` passes a per-run project_dir to `runner.run()`, or a new `cwd=` kwarg on `runner.run()`.

- **DEC-009 — `output_files` collision guard (C3, R4-B).** At spec load, if any `output_files` pattern literally matches an `input_files` basename, raise `ValueError`. Plus a README/example warning. **Why:** staging dir = CWD means stale input globs are a silent footgun. **How to apply:** check in `EvalSpec.from_file` after both fields are parsed.

- **DEC-010 — Variance fresh copies (Q5-A, R5-A).** Each `run-K/inputs/` is populated by re-copying from the spec-dir source (absolute path stored on the spec). No intermediate master cache. **Why:** simplest code path, guaranteed pristine inputs, negligible disk cost. **How to apply:** `stage_inputs()` is called once per run inside the variance loop in `cli._cmd_grade_with_workspace`.

- **DEC-011 — Captured-output mode (R6-C).** `EvalSpec.from_file` validation still fires in `--output <file>` mode (fail-fast). At the CLI layer, if `--output` is combined with a non-empty `input_files`, print a warning to stderr and skip staging (no run happens). **Why:** spec correctness shouldn't depend on how the user invokes grade; warning tells them the declaration is inert in this mode. **How to apply:** warning lives in `cli.cmd_grade` where `--output` is handled.

- **DEC-012 — Pytest fixture transparent staging (Q6-A).** The `clauditor_spec` factory stages `input_files` into `tmp_path / "inputs/"` and wires that as the runner's `cwd`. No new fixture. Tests that want explicit control can still build their own `SkillSpec` by hand. **Why:** smallest public surface; mirrors the CLI's behavior so tests match production. **How to apply:** extend `pytest_plugin.py:clauditor_spec`.

- **DEC-013 — Mutation capture via existing `output_files` (Q3-A).** No auto-diff, no read-only staging. If a user wants to capture a mutated input, they declare it in `output_files` — but because of DEC-009, the spec-load guard will force them to rename so it can't collide with the input basename. **Why:** reuses existing machinery; simpler than diffing. **How to apply:** documented in README + example spec.

- **DEC-014 — No file-size cap.** Staged files are copied without size checks. Documented in README as "input files are copied without size limits". Future work: optional `max_input_bytes` field. **Why:** pre-production, author-trusted specs; matches existing "no output size limit" posture. **How to apply:** docs only.

- **DEC-015 — Logging style.** One stdout line per run: `Staged N input file(s) into run-K/inputs/`. No prefix, matches `cli.py:62`. Errors follow `EvalSpec(skill_name='foo'): input_files[1]='missing.csv' — <reason>` pattern (schemas.py:32-46). **Why:** house style consistency.

- **DEC-016 — Trust model note in README.** One-paragraph addition stating eval specs are developer-authored and run with the repo owner's filesystem access. **Why:** makes the "author-trusted" assumption explicit so downstream consumers (e.g. public example galleries) know the review bar.

### Session notes

- All 6 scoping questions and 6 refinement questions answered in one session (2026-04-13) with user accepting every recommendation.
- No rules in `.claude/rules/`; no `workflow-project.md`. Pre-production posture — no backward-compat shims needed.
- Architecture review surfaced 2 blockers (both path-traversal flavors) and 7 concerns; all resolved by DEC-003..DEC-009 + docs.

---

## Detailed Breakdown

Layering follows the natural clauditor stack: schema → workspace helper → spec/runner glue → CLI integration → pytest plugin → docs → quality gate → patterns.

### US-001 — `EvalSpec.input_files` field + load-time validation

**Description.** Add the `input_files` field to `EvalSpec` and implement full load-time validation in `EvalSpec.from_file()`: reject absolute paths, resolve against `path.parent`, enforce source containment (including symlink targets), flatten destinations, detect duplicate basenames, and enforce the `output_files` collision guard.

**Traces to.** DEC-001, DEC-002, DEC-003, DEC-004, DEC-005, DEC-006, DEC-009, DEC-015 (error-message style).

**Acceptance criteria.**
- `EvalSpec` has `input_files: list[str] = field(default_factory=list)` declared after `test_args`.
- `EvalSpec.from_file(path)` stores absolute path strings in `input_files`.
- Relative entries are resolved against `path.parent`.
- Each of these conditions raises `ValueError` with an informative message citing `skill_name` and the offending index: absolute path entry; entry that escapes the spec dir via `..`; entry whose symlink target escapes the spec dir; missing file; two entries with the same basename; any `output_files` pattern literal-matching an input basename.
- Old specs without an `input_files` key load cleanly (default `[]`).
- `to_dict()` round-trips the field.
- `uv run ruff check src/ tests/` passes.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with ≥80% coverage.

**Done when.** All new tests green; coverage gate met; `test_schemas.py::TestEvalSpecInputFiles` covers every `ValueError` branch above.

**Files.**
- `src/clauditor/schemas.py` — add field at ~L122; extend `EvalSpec.from_file` with validation block; extend `to_dict`.
- `tests/test_schemas.py` — new `TestEvalSpecInputFiles` class.

**Depends on.** none.

**TDD.**
- `test_input_files_defaults_to_empty_list` — spec without the key loads with `[]`.
- `test_relative_paths_resolved_against_spec_dir` — `"sales.csv"` → absolute under spec dir.
- `test_absolute_path_rejected` — `"/etc/passwd"` raises `ValueError`.
- `test_path_traversal_rejected` — `"../../../etc/passwd"` raises.
- `test_symlink_target_outside_spec_dir_rejected` — symlink in spec dir → target outside → raises.
- `test_symlink_target_inside_spec_dir_accepted` — link to sibling file works.
- `test_missing_input_file_raises_valueerror_at_load` — mention of `input_files[i]` and filename.
- `test_duplicate_basenames_across_entries_rejected` — `["a/sales.csv", "b/sales.csv"]` raises.
- `test_output_files_collision_guard_rejects_overlap` — `input_files: ["x.csv"]`, `output_files: ["x.csv"]` raises.
- `test_to_dict_roundtrip_preserves_input_files`.

---

### US-002 — `workspace.stage_inputs()` helper

**Description.** Add a pure helper that, given a run-scoped destination directory and a list of absolute source paths, copies each source to `<dest>/<basename>` with `shutil.copy2`, creating `dest` if needed. No path parsing or validation — that's US-001's job. Returns the list of destination paths.

**Traces to.** DEC-007, DEC-010 (fresh copies), DEC-013 (no special read-only handling).

**Acceptance criteria.**
- New `stage_inputs(run_dir: Path, sources: list[Path]) -> list[Path]` in `src/clauditor/workspace.py`.
- Creates `run_dir / "inputs"` if missing.
- Empty `sources` → no-op, returns `[]`, does not create the `inputs/` dir.
- Preserves file content and mtime (`copy2`).
- Raises `FileNotFoundError` with a clear message if a source no longer exists (unlikely post-US-001, but defensive).
- `uv run pytest tests/test_workspace.py` passes; `uv run ruff` clean.

**Done when.** `tests/test_workspace.py::TestStageInputs` covers empty / single / multiple / missing-source / destination-creation cases, all green; function has 100% line coverage.

**Files.**
- `src/clauditor/workspace.py` — append new helper (no changes to `allocate_iteration`).
- `tests/test_workspace.py` — new `TestStageInputs` class using `tmp_path`.

**Depends on.** US-001 (for typing consistency, not strictly required to compile — the helper is freestanding).

**TDD.**
- `test_stage_inputs_empty_list_is_noop` — no dir created.
- `test_stage_inputs_single_file_copies_and_preserves_mtime`.
- `test_stage_inputs_multiple_files`.
- `test_stage_inputs_creates_inputs_subdir`.
- `test_stage_inputs_missing_source_raises`.

---

### US-003 — `SkillRunner` cwd override + `SkillSpec.run()` staging hook

**Description.** Allow `SkillRunner.run()` (or `_invoke`) to accept a per-call `cwd` override. Update `SkillSpec.run()` to detect a non-empty `input_files`, stage them via `stage_inputs()` into a per-run directory passed in by the caller, and invoke the runner with `cwd` pointed at that `inputs/` directory. When `input_files` is empty, preserve today's behavior exactly (runner uses `project_dir`).

**Traces to.** DEC-007, DEC-008.

**Acceptance criteria.**
- `SkillRunner.run(skill_name, args, *, cwd: Path | None = None)` — when `cwd` is `None`, uses `self.project_dir` (current behavior, unchanged).
- `SkillSpec.run()` accepts an optional `run_dir: Path | None` kwarg; if provided and `input_files` is non-empty, stages inputs into `run_dir / "inputs"` and passes that as `cwd` to the runner.
- When `run_dir` is `None` or `input_files` is empty, behavior is bit-for-bit identical to today.
- One stdout info line per staging call: `Staged N input file(s) into <relative path>` (DEC-015).
- Existing runner/spec tests still pass (no regression).
- `ruff` clean; coverage ≥80%.

**Done when.** New tests cover both the cwd-override path (via Popen mock assertion on the `cwd=` kwarg) and the staging integration in `SkillSpec.run()`.

**Files.**
- `src/clauditor/runner.py` — add `cwd` kwarg to `run()`/`_invoke`; thread to `Popen(..., cwd=...)`.
- `src/clauditor/spec.py` — extend `SkillSpec.run()` signature; call `stage_inputs` when appropriate; emit info log.
- `tests/test_runner.py` — extend `TestSkillRunnerRun` with `test_run_uses_cwd_override_when_passed`.
- `tests/test_spec.py` — new `TestSkillSpecRunWithInputFiles` covering empty / populated / log-line assertions.

**Depends on.** US-001, US-002.

**TDD.**
- `test_runner_default_cwd_is_project_dir` (regression guard).
- `test_runner_cwd_override_passed_to_popen`.
- `test_skillspec_run_with_empty_input_files_uses_project_dir`.
- `test_skillspec_run_with_input_files_stages_and_sets_cwd`.
- `test_skillspec_run_emits_staged_log_line`.

---

### US-004 — CLI `grade` integration + variance fresh copies + captured-output warning

**Description.** Wire US-003 into `cli._cmd_grade_with_workspace`. For each run in the variance loop, compute the tmp run directory (`iteration-N-tmp/<skill>/run-K/`), pass it to `spec.run()` so inputs are re-staged fresh per run (DEC-010). When `--output <file>` is combined with a non-empty `input_files`, emit a stderr warning and skip staging entirely (DEC-011). After `workspace.finalize()`, the staged inputs live at their final persistent path.

**Traces to.** DEC-007, DEC-010, DEC-011.

**Acceptance criteria.**
- Single-run and variance modes both pass a per-run directory to `spec.run()`.
- `iteration-N/<skill>/run-K/inputs/` contains the staged input files after `finalize()`.
- In variance mode, each `run-K/inputs/` is a separately-copied, independently-writable directory (mutation in run-0 doesn't affect run-1).
- `clauditor grade --output captured.txt` on a spec with `input_files` prints `WARNING: --output bypasses the runner; input_files declaration is ignored.` to stderr and does not attempt to stage.
- Existing non-input_files grade flows unchanged (regression guard).
- `ruff` clean; coverage ≥80%.

**Done when.** CLI tests assert the per-run staging paths on disk, the variance isolation, and the captured-output warning text.

**Files.**
- `src/clauditor/cli.py` — inside the variance loop in `_cmd_grade_with_workspace`, thread `run_dir` into `spec.run()`; add the `--output` + `input_files` warning branch in `cmd_grade`.
- `tests/test_cli.py` — extend `TestCmdGrade` with two tests (see TDD below).

**Depends on.** US-003.

**TDD.**
- `test_grade_stages_inputs_into_iteration_run_dir_on_finalize` — run a fake skill end-to-end with one input file, assert `iteration-1/<skill>/run-0/inputs/sales.csv` exists with correct bytes.
- `test_grade_variance_stages_inputs_per_run` — `variance.n_runs=3`, mutate input in each fake run, assert each `run-K/inputs/sales.csv` holds its own copy.
- `test_grade_captured_output_mode_warns_and_skips_staging` — `--output captured.txt` with `input_files: ["x.csv"]` prints the warning and does not create any `inputs/` dir.

---

### US-005 — Pytest plugin transparent staging

**Description.** Extend the `clauditor_spec` fixture factory in `pytest_plugin.py` so that when a loaded `SkillSpec` has non-empty `input_files`, the factory automatically stages them into `tmp_path / "inputs/"` and arranges for `SkillSpec.run()` to use that directory as the runner cwd. No new fixture; tests see the feature transparently.

**Traces to.** DEC-012.

**Acceptance criteria.**
- `clauditor_spec(skill_path, eval_path=None)` returns a `SkillSpec` whose subsequent `.run()` call stages inputs into the test's `tmp_path`.
- Tests that don't touch `input_files` see zero behavior change (regression).
- Captured-output fixture path (`clauditor_capture`) is unaffected.
- `ruff` clean; fixture is covered by at least one pytester-style integration test and one direct unit test.

**Done when.** `tests/test_pytest_plugin.py` has new tests demonstrating the transparent staging and a non-regression test for the existing fixture behavior.

**Files.**
- `src/clauditor/pytest_plugin.py` — extend the `clauditor_spec` factory.
- `tests/test_pytest_plugin.py` — new tests under `TestPytestPlugin`.

**Depends on.** US-003.

**TDD.**
- `test_clauditor_spec_fixture_stages_input_files_into_tmp_path` (pytester).
- `test_clauditor_spec_fixture_without_input_files_is_unchanged` (regression).

---

### US-006 — Docs: README, example eval spec, trust-model note

**Description.** Update README and the example eval spec to document `input_files`: placement in the field list, semantics (CWD, flattening, containment, symlink handling), the `output_files` collision guard, the captured-output mode warning, the no-size-limit disclaimer, and a one-paragraph trust-model note. Update the agentskills.io alignment table to mark this gap closed.

**Traces to.** DEC-006 (flatten), DEC-008 (CWD), DEC-009 (collision guard), DEC-011 (captured-output), DEC-013 (mutation via output_files), DEC-014 (size), DEC-016 (trust note).

**Acceptance criteria.**
- README has a dedicated `input_files` subsection with a small worked example (CSV cleaner skill).
- README mentions: relative-to-spec-dir resolution, flatten-to-basename, containment, symlink target check, reject absolute, `output_files` collision, captured-output warning, no size limit.
- README trust-model paragraph states specs are author-controlled.
- `examples/.claude/commands/example-skill.eval.json` gains an `input_files` entry (or a separate example file is added) demonstrating the feature.
- Agentskills.io alignment gap #5 marked closed.
- No code changes in this story — docs only.

**Done when.** `git diff` shows only README + example JSON + (optional) `docs/` updates.

**Files.**
- `README.md`
- `examples/.claude/commands/example-skill.eval.json` (or a sibling example).

**Depends on.** US-001..US-005 (docs describe behavior that must exist).

---

### US-Q — Quality Gate

**Description.** Run the code reviewer over the full changeset 4 times in sequence, fixing every real bug each pass. Then run CodeRabbit (`coderabbit review --base dev`) and address findings. Finally re-run `uv run ruff check src/ tests/` and `uv run pytest --cov=clauditor --cov-report=term-missing`; confirm ≥80% coverage and green.

**Traces to.** Project convention from plan #22 Quality Gate.

**Acceptance criteria.**
- 4 code-review passes completed; all real bugs fixed (not stylistic nits unless they reflect a real rule).
- CodeRabbit findings triaged (addressed / explicitly skipped with reason).
- `ruff` clean, `pytest` green, coverage ≥80%.
- Commit history shows the review passes as distinct fixup commits.

**Done when.** Final review pass turns up zero real bugs and all gates are green.

**Depends on.** US-001..US-006.

---

### US-P — Patterns & Memory

**Description.** Capture any new patterns or gotchas learned during implementation. Candidates: the source-containment pattern for user-provided paths (reusable for any future field that accepts filesystem paths); the CWD-override idiom on `SkillRunner`; the load-time-vs-run-time resolution split; any new test helpers that emerged.

**Traces to.** DEC-003 (containment pattern — reusable), DEC-008 (cwd override idiom).

**Acceptance criteria.**
- `.claude/rules/` updated or created with at least a path-containment rule if no equivalent exists.
- `docs/` or a `bd remember` entry captures the load-vs-run-time resolution decision so the next person touching `EvalSpec` doesn't re-debate it.
- No code changes unless a refactor naturally falls out.

**Depends on.** US-Q.


---

## Beads Manifest

- **Epic:** `clauditor-5o9` — #23: Input files support on eval specs
- **Draft PR:** https://github.com/wjduenow/clauditor/pull/32
- **Branch / worktree:** `feature/23-eval-input-files` at repo root
- **Tasks (parent = epic):**
  - `clauditor-5o9.1` — US-001 — EvalSpec.input_files field + load-time validation *(no deps, ready)*
  - `clauditor-5o9.2` — US-002 — workspace.stage_inputs() pure copy helper *(blocked by .1)*
  - `clauditor-5o9.3` — US-003 — SkillRunner cwd override + SkillSpec.run() staging hook *(blocked by .1, .2)*
  - `clauditor-5o9.4` — US-004 — CLI grade integration, variance fresh copies, captured-output warning *(blocked by .3)*
  - `clauditor-5o9.5` — US-005 — Pytest plugin transparent staging *(blocked by .3)*
  - `clauditor-5o9.6` — US-006 — Docs: README, example eval spec, trust-model note *(blocked by .1..5)*
  - `clauditor-5o9.7` — Quality Gate — code review x4 + CodeRabbit + ruff + pytest *(blocked by .1..6)*
  - `clauditor-5o9.8` — Patterns & Memory *(blocked by .7, priority 4)*
- **Parallel-safe at start:** `clauditor-5o9.1` only (everything else depends on it).
- **After US-001 lands:** `.2` unblocks; then `.3`; then `.4` and `.5` run in parallel.
