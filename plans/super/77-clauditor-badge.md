# Super Plan: #77 — `clauditor badge` command (shields.io endpoint JSON)

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/77
- **Branch:** `feature/77-clauditor-badge`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/77-clauditor-badge`
- **Phase:** `devolved`
- **PR:** https://github.com/wjduenow/clauditor/pull/81
- **Epic:** `clauditor-wnv`
- **Sessions:** 1
- **Last session:** 2026-04-21

---

## Discovery

### Ticket summary

**What:** A new `clauditor badge <skill-path> [options]` CLI subcommand
that generates a shields.io-compatible JSON endpoint file from a
skill's latest (or `--from-iteration N`) iteration sidecars.
Shields.io renders the SVG from the JSON; users embed a single
Markdown image line (the URL is constant; only the JSON content
updates per CI run).

**Why:** Teams publishing skills want a one-glance quality signal
next to the skill in their README (or in a skill catalog page).
Per-run scores clauditor already computes (`AssertionSet.pass_rate`,
`GradingReport.pass_rate` + `mean_score` + threshold-based `passed`,
optional `VarianceReport.stability`) are exactly the data a badge
needs. The shields.io endpoint pattern is the cheapest path — no SVG
rendering, no hosting, no PR comment plumbing.

**Done when:**
- `clauditor badge src/skills/<name>/SKILL.md` writes a
  shields.io-compatible JSON file under
  `.clauditor/badges/<skill>.json` (default) with top-level
  `schemaVersion`, `label`, `message`, `color` + a nested
  `clauditor:` extension block carrying full state.
- Color and message reflect L1 + L3 + (optional) variance results
  per the ticket's table.
- `--url-only` prints a README-ready Markdown image line and exits
  without writing JSON.
- `--from-iteration N` selects a specific iteration.
- `--output PATH`, `--label TEXT`, `--style KEY=VALUE` (repeatable)
  pass-throughs land.
- Exit codes follow the project convention (0 success, 1 load-time
  failure / missing sidecar, 2 input validation).
- `ruff` passes; coverage stays ≥80%; no new lint suppressions.

### Key findings — codebase scout

#### Sidecar sources (already persisted, already versioned)

- **`.clauditor/iteration-N/<skill>/assertions.json`** — L1.
  Structure holds `results: [...]`, `input_tokens`, `output_tokens`
  and carries `schema_version: 1`. Aggregates via
  `AssertionSet.pass_rate` (`sum(passed) / len(results)`) and
  `AssertionSet.passed` (`all(passed)`) — see
  `src/clauditor/assertions.py` lines 78–132.
- **`.clauditor/iteration-N/<skill>/grading.json`** — L3.
  `GradingReport.pass_rate`, `.mean_score`, `.thresholds` (the
  `GradeThresholds{min_pass_rate, min_mean_score}` dataclass defined
  in `src/clauditor/schemas.py` lines 199–203, defaults 0.7 / 0.5),
  and `.passed` (pass_rate ≥ min_pass_rate AND mean_score ≥
  min_mean_score). Defined in
  `src/clauditor/quality_grader.py` lines 52–159.
- **`.clauditor/iteration-N/<skill>/extraction.json`** — L2. Not
  consumed by the badge in v1 (see ticket open question #3).
- **Variance sidecar**: NOT currently written anywhere. The codebase
  scout verified `VarianceConfig` exists on `EvalSpec` but no
  `variance.json` writer ships today. The ticket's variance layer
  assumes a sidecar that does not yet exist.

#### Iteration discovery helper

`src/clauditor/audit.py::load_iterations` walks
`.clauditor/iteration-N/` dirs (discovered by `_scan_iteration_dirs`,
sorted descending by iteration number), reads each sidecar via
`_read_json` (returns `None` on missing / parse error), checks
`_check_schema_version` before consuming. Pattern for "latest
iteration for skill X": call `_scan_iteration_dirs` and find the
highest N where `iteration-N/<skill>/` exists. Per-sidecar loaders
(`_records_from_assertions`, `_records_from_extraction`,
`_records_from_grading`) are the closest reference for safe
dict → dataclass conversion.

#### Closest pure-compute-vs-I/O anchors

- `src/clauditor/baseline.py::compute_baseline` — pure function taking
  pre-run result dataclasses, returns `BaselineReports` with
  `.to_json_map()` → `{filename: json_str}`. Best structural match
  for "compute an aggregate dataclass from sidecars + serialize".
- `src/clauditor/benchmark.py::compute_benchmark` — pure aggregator
  returning a `Benchmark` dataclass with `.to_json()` (schema_version
  first). Shows the schema-version-first-key discipline.
- `src/clauditor/setup.py::plan_setup` — pure decision function
  returning an enum for the CLI layer to dispatch on. Not a data
  aggregator, but demonstrates the pure-helper shape for a CLI-only
  feature.

#### CLI command recipe

Every subcommand under `src/clauditor/cli/` exposes two symbols:

```python
def add_parser(subparsers: argparse._SubParsersAction) -> None: ...
def cmd_<name>(args: argparse.Namespace) -> int: ...
```

Dispatcher wiring lives in `src/clauditor/cli/__init__.py::main`:
lazy-import the module, call `<mod>.add_parser(subparsers)` before
`args = parser.parse_args()`, and add an `elif parsed.command ==
"<name>": return cmd_<name>(parsed)` branch in the dispatch block.
`cmd_*` returns the exit code; stderr-facing errors use `print(...,
file=sys.stderr)`.

#### Existing test shape to mirror

- `tests/test_benchmark.py::TestComputeBenchmark` — pure-helper tests
  with dataclass fixtures, no `tmp_path`, no mocks.
- `tests/test_baseline.py::TestComputeBaseline` — same shape, with
  async-grader mocks since `compute_baseline` awaits.
- CLI integration: `tests/test_cli_suggest.py` style — `capsys` for
  stdout/stderr, `tmp_path` for output path fixtures.

### Key findings — convention checker (rules that apply)

All `.claude/rules/*.md` files were read. Rule constraints that
apply to this feature:

1. **`json-schema-version.md`** — The badge JSON has TWO version
   fields:
   - Top-level `schemaVersion: 1` (shields.io's schema — required by
     them, not us).
   - Nested `clauditor.schema_version: 1` (our extension block).
   The nested version follows the project's discipline: first key of
   the `clauditor:` block; any future bump requires matching loader
   logic. No loader exists yet for the badge JSON (we only write it),
   but a future `clauditor badge --read` or trend-audit consumer
   would follow `_check_schema_version`.
2. **`pure-compute-vs-io-split.md`** — `src/clauditor/badge.py` is
   the pure module (`compute_badge(...)` takes pre-parsed dicts,
   returns a `Badge` dataclass with `to_endpoint_json()`).
   `src/clauditor/cli/badge.py` owns all I/O (sidecar reads, output
   writes, stderr printing, exit-code mapping).
3. **`llm-cli-exit-code-taxonomy.md`** — Badge makes NO LLM call, so
   use the simpler 0/1/2 taxonomy (not 0/1/2/3). Exit 0 on success,
   1 on load-time / parse-layer / disk failure, 2 on input
   validation. No `api_error` field, no `AnthropicHelperError` path.
4. **`path-validation.md`** — If `--output` accepts a user-provided
   path, validate via the existing recipe (non-empty string, not
   absolute OR allow absolute explicitly — decision point, flagged
   as Q5 below, `resolve(strict=False)` because the output path may
   not exist yet, `is_file()` is wrong since we're writing not
   reading).
5. **`in-memory-dict-loader-path.md`** — Sidecars are already
   finalized on disk; `compute_badge` accepts parsed `dict`s, not
   file paths. No `from_dict`/`from_file` split needed (sidecars are
   clauditor-owned, not LLM-proposed).
6. **`constant-with-type-info.md`** — The color-logic table is a
   fixed map from classification → color string. If a dataclass
   constant emerges (e.g., `BadgeStatus` with typed fields), declare
   `field_types` per the rule; for a flat string-valued constant the
   rule is lighter-weight.
7. **`skill-identity-from-frontmatter.md`** — The skill argument is
   a SKILL.md path; pass through `SkillSpec.from_file` (which already
   uses `derive_skill_name`) to get the `skill_name`. Do not
   re-implement frontmatter parsing.
8. **`project-root-home-exclusion.md`** — N/A if the CLI argument
   provides an explicit skill path. Relevant only if we add a
   default "discover project root" behavior (which we are NOT, in
   v1).
9. **`sidecar-during-staging.md`** — Badge reads *finalized*
   iterations; the badge JSON output lives outside the iteration
   tree (at `.clauditor/badges/<skill>.json`), so staging discipline
   does not apply to the output. Reads of iteration sidecars happen
   after `workspace.finalize()` has already renamed the iteration.
10. **`readme-promotion-recipe.md`** — Badge docs may grow enough to
    promote to `docs/badge-reference.md`. The root README teaser
    (D2 lean) + `docs/badges.md` or `docs/badge-reference.md` full
    reference pattern applies.
11. **`bundled-skill-docs-sync.md`** — Unlikely to apply in v1; the
    bundled `/clauditor` SKILL.md does not currently invoke `badge`
    as a workflow step. If a future PR teaches the skill to run
    `clauditor badge` after `grade`, the three-way sync (SKILL.md +
    skill-usage.md + README + cli-reference.md) applies.
12. **`non-mutating-scrub.md`** — N/A for v1 (no redaction path).
    Flag for future `--redact-evidence` flag if anyone ever wants to
    publish a badge without leaking skill-output snippets.

### Proposed scope

**In scope (v1)**
- Pure `compute_badge` + `Badge` dataclass with `to_endpoint_json()`
  and `to_json()`.
- CLI command `clauditor badge <skill-path>` with flags:
  `--from-iteration N`, `--output PATH`, `--url-only`,
  `--style KEY=VALUE`, `--label TEXT`.
- L1 (required), L3 (optional), variance (optional, graceful degrade
  when sidecar absent, which is today's steady state).
- Color logic per ticket table; message format per ticket.
- Exit codes 0 / 1 / 2.
- Unit tests (`tests/test_badge.py`) — per-branch color logic +
  round-trip schema + threshold edge cases.
- CLI integration tests (`tests/test_cli_badge.py`) — capsys for
  `--url-only`, tmp_path for `--output`, exit-code assertions.
- Docs: `docs/cli-reference.md#badge` subsection + new
  `docs/badges.md` covering placement tradeoffs (README vs SKILL.md
  vs catalog page) per the ticket's "Can badges go on SKILL.md
  files?" discussion.

**Out of scope (defer)**
- Multi-skill catalog badge (ticket open question #2) — issue
  separately.
- L2 inclusion in badge message (ticket open question #3) — issue
  separately if requested.
- Blind-compare A/B badge (ticket open question #4) — separate
  command.
- GitHub Action that commits badges back — ticket "optional phase
  2"; defer.
- Auto-mutating README to embed the badge line — ticket non-goal.
- Variance sidecar writer — out of this epic; badge gracefully
  degrades today.

### Scoping questions

**Q1. "No iteration found" behavior.** When a user runs `clauditor
badge <skill>` but no `iteration-N/<skill>/` exists:

- **A.** Exit 1 with a stderr error (`"no iteration found for
  skill X — run clauditor validate/grade first"`). No badge JSON
  written. Simplest.
- **B.** Write a `lightgrey` "no data" badge JSON and exit 0.
  Matches the ticket's color table row (`"No iteration exists for
  this skill" → lightgrey`). CI pipelines that always run
  `clauditor badge` then commit get a persistent placeholder.
- **C.** Default to exit 1, but add `--allow-empty` flag that
  writes the lightgrey placeholder. Explicit opt-in for CI.
- **D.** `--url-only` mode always prints (it needs no data, just
  the skill name for the filename in the URL). JSON-writing mode
  exits 1 when no iteration.

**Q2. `--url-only` URL construction.** The ticket's example is
`![clauditor](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/USER/REPO/main/.clauditor/badges/<skill>.json)`.
How does the command know the user's repo path?

- **A.** Print a placeholder (`USER/REPO/main`) — user edits once
  when pasting. Ticket's literal example does this. Zero magic, no
  network, no git dependency.
- **B.** Auto-detect from `git remote get-url origin` + `git
  symbolic-ref refs/remotes/origin/HEAD`. Fall back to placeholder
  if detection fails. Nice UX; adds subprocess complexity.
- **C.** Require `--repo USER/REPO [--branch main]` flags.
  Explicit, testable, but user has to remember the flags.
- **D.** Hybrid: auto-detect by default; placeholder on failure;
  `--repo` flag to override auto-detect.

**Q3. Variance layer.** The ticket spec includes `layers.variance`
and the `80% stable` message fragment. But no `variance.json` writer
exists today.

- **A.** Include it in the schema with graceful degrade — badge
  reads `variance.json` if present, omits the block if not. Today
  the block is always omitted; if a future ticket adds the
  writer, badge "just works".
- **B.** Defer variance entirely from v1 — do not mention
  `layers.variance` in the schema at all until the writer exists.
  File variance as a separate issue.
- **C.** Block on variance sidecar landing — close this issue as a
  dependency on the variance-writer issue and come back.

**Q4. L3 threshold source.** `GradingReport` already carries its
own `thresholds` (the ones the grade was evaluated against,
persisted in `grading.json`). The ticket mentions a possible
`--thresholds min_pass_rate=0.8` flag for override. What should
the badge's L3 `passed` reflect?

- **A.** Use `grading.json`'s own `thresholds` block — the badge
  shows what the grade already decided. No CLI override. The
  source of truth is the sidecar.
- **B.** Load `EvalSpec.grade_thresholds` from the spec fresh;
  fall back to defaults (0.7 / 0.5). No CLI override. Allows
  "reinterpret the grade with the current eval spec" semantics
  when the spec evolves.
- **C.** Option A by default, but `--thresholds min_pass_rate=X
  [--thresholds min_mean_score=Y]` overrides for the badge only.
  Users can show a harsher/softer bar on the badge than the
  grade used.
- **D.** Let the user pick at call-time: `--threshold-source
  sidecar|spec|cli`, default `sidecar`.

**Q5. Output path location and policy.** Default is
`.clauditor/badges/<skill>.json` per the ticket. Two sub-questions:

- **Q5a. Path policy for `--output PATH`:**
  - **A.** Allow absolute paths (user may write to `~/public/` or
    a docs site dir).
  - **B.** Reject absolute paths (per the spirit of
    `path-validation.md`); keep writes rooted at project root.
  - **C.** Allow absolute if `--force-absolute` is set; default is
    relative-only.

- **Q5b. Default location:**
  - **A.** `.clauditor/badges/<skill>.json` (per ticket) — dedicated
    badges dir, persists across iterations.
  - **B.** `.clauditor/iteration-N/<skill>/badge.json` — inside the
    iteration (colocated with other sidecars, but the URL changes
    per iteration → badge URL would have to point to a "latest"
    symlink or to the current iteration number).
  - **C.** Next to the SKILL.md (`<skill-dir>/badge.json`) — badge
    lives with the skill source, naturally picked up by README
    relative links.

**Q6. Color edge cases.** Beyond the ticket's four rows:

- **Q6a. Iteration exists but has zero L1 assertions** (a spec that
  declares only L3 criteria):
  - **A.** `lightgrey` (no L1 signal).
  - **B.** L1 is trivially green (0/0 passes), badge reflects L3
    only.
  - **C.** Error — the spec must declare ≥1 L1 assertion for the
    badge to make sense.

- **Q6b. Iteration exists but `assertions.json` is missing** (shouldn't
  normally happen post-`validate`):
  - **A.** Exit 1 (corrupt iteration).
  - **B.** Treat as `lightgrey` / "no data".

- **Q6c. L3 run but all parse-failed** (grading couldn't score):
  - **A.** `red` — grading failure is a failure.
  - **B.** Treat as L3 absent (omit the L3 fragment).

### Discovery-phase proposed defaults

Before the user answers, my leaning (to pressure-test each option):

- **Q1 → A.** Exit 1 is the simplest default. Teams that want a
  persistent placeholder can follow up with `--allow-empty` later;
  bundling it into v1 bloats the surface.
- **Q2 → D.** Auto-detect with `--repo` override. `git remote
  get-url origin` is one subprocess call, the failure path falls
  through to the placeholder (same as A), and `--repo` is the
  testable explicit path.
- **Q3 → A.** Graceful degrade. The badge schema already allows
  optional blocks; supporting `variance.json` when it eventually
  ships is additive and costs almost nothing now.
- **Q4 → A.** Use sidecar's own thresholds. Source of truth is the
  grade that actually ran; reinterpreting at badge-time invites
  drift between "what the grade said" and "what the badge shows."
  CLI override can be added later if requested.
- **Q5a → A.** Allow absolute paths. The badge JSON often lands
  outside the repo (e.g. in a GitHub Pages dir at
  `~/work/pages-site/badges/`); blocking absolute paths here
  creates real friction for a benign use case.
- **Q5b → A.** `.clauditor/badges/<skill>.json` per ticket.
  Persistent location, stable URL, outside the iteration tree.
- **Q6a → A.** `lightgrey`. A spec with zero L1 assertions is
  unusual; surfacing that as "no data" is clearer than faking a
  trivially-green pass.
- **Q6b → A.** Exit 1. Corrupt iteration.
- **Q6c → A.** `red`. A graded-but-unscorable run is a failure
  signal worth surfacing.

---

## Architecture Review

### Scoping answers (Phase 1 close-out)

- **Q1 → B.** No iteration found → write `lightgrey` "no data" badge,
  exit 0 (persistent placeholder for CI pipelines).
- **Q2 → D.** Hybrid `--url-only`: auto-detect via `git remote
  get-url origin` + default branch; `--repo`/`--branch` overrides;
  placeholder on detection failure.
- **Q3 → A.** Variance block graceful-degrades — include when
  `variance.json` sidecar exists, omit when absent (today's steady
  state).
- **Q4 → A.** L3 thresholds come from `grading.json`'s own
  `thresholds` block; no CLI override.
- **Q5a → A.** `--output PATH` accepts absolute paths.
- **Q5b → A.** Default output `.clauditor/badges/<skill>.json`.
- **Q6a → A.** Zero L1 assertions → `lightgrey`.
- **Q6b → A.** `assertions.json` missing from an otherwise-present
  iteration → exit 1 (corrupt iteration).
- **Q6c → A.** L3 all parse-failed → `red`.

### Review ratings

| Area | Rating | Summary |
|---|---|---|
| Security | concern | `--output` parent-dir validation; git subprocess error handling; `--style` URL encoding |
| Data model | concern (+ 1 blocker) | L1/L3 field-naming inconsistency (blocker); `generated_at` timestamp format; dataclass nesting shape |
| API / CLI design | concern (+ 1 blocker) | Missing `--force` flag (blocker); `--url-only`+`--output` interaction; `--style` parsing; `--from-iteration N` missing behavior |
| Testing strategy | concern | Schema-drift guard via fixture round-trip; git subprocess mocking shape; 0/0 edge-case message |
| Observability | pass | Silent-by-default + `--verbose`; stderr warnings for placeholder and git fallback |
| Performance | pass | All costs negligible (<100ms even with git call); no caching needed |

### Blockers (must resolve before Phase 4)

- **BLK-1 (data model) — L1 field naming.** Ticket shape has
  `clauditor.layers.l1.all_passed: bool` but
  `clauditor.layers.l3.passed: bool`. `AssertionSet.passed` in the
  existing code is `all(r.passed for r in results)` (equals
  `all_passed`); `GradingReport.passed` is threshold-based. The
  two `passed` fields on the badge would mean different things. Pick
  a convention and document.

- **BLK-2 (API) — Overwrite policy.** Existing commands that write
  persisted JSON (`propose_eval`, `init`) require `--force` to
  overwrite an existing file; otherwise exit 1. Badge has no such
  flag defined. A silent overwrite diverges from project convention
  and could blow away a user's badge on an accidental re-run.

### Concerns (to resolve in Phase 3)

- **C-1 (data model) — `generated_at` ISO-8601 format.** Ticket
  shows `"2026-04-21T14:00:00Z"`; Python's
  `datetime.now(timezone.utc).isoformat()` produces
  `"2026-04-21T14:00:00+00:00"`. Both are valid; shields.io accepts
  both. Pick one for canonical output.

- **C-2 (data model) — Dataclass nesting shape.** Existing anchor
  `Benchmark` in `src/clauditor/benchmark.py` uses nested dataclasses
  (`RunSummary`, etc.) rather than raw nested dicts. The badge's
  `clauditor:` extension block has three optional sub-layers (l1,
  l3, variance); idiomatic choice is nested dataclasses with
  `to_dict()` methods vs. a single `Badge` carrying raw dicts for
  the nested layers.

- **C-3 (API) — `--url-only` + `--output` interaction.** Ticket
  treats these as mutually exclusive ("do NOT write the JSON"). Need
  an explicit mutual-exclusion check returning exit 2 rather than
  silent precedence.

- **C-4 (API) — `--style KEY=VALUE` validation.** No prior art in
  the codebase for this flag shape. Options:
  - **A.** Whitelist shields.io keys (`style`, `logoSvg`,
    `logoColor`, `labelColor`, `cacheSeconds`, `link`) with a
    stderr warning on unknown (non-fatal; ship the badge anyway
    since shields.io silently ignores).
  - **B.** Blind passthrough — any key/value goes into the JSON.
  - **C.** Whitelist with exit 2 on unknown.

- **C-5 (API) — `--from-iteration N` missing behavior.** If a user
  explicitly asks for iteration 42 and it doesn't exist, that's
  different from DEC-001 "no iteration exists at all". Options:
  - **A.** Exit 1 with `"iteration 42 not found for skill X"`.
    Explicit request means explicit failure.
  - **B.** Same as DEC-001 — write lightgrey, exit 0.
  - **C.** Exit 2 — treat as user-input error.

- **C-6 (testing) — Git subprocess wrapper.** `--url-only` needs
  `git remote get-url origin` + default-branch detection. Two
  patterns:
  - **A.** Extract `src/clauditor/_git.py` wrapper (`get_repo_url()
    -> str | None`, `get_default_branch() -> str | None`); tests
    patch the wrapper, not subprocess.
  - **B.** Call `subprocess.run` directly in
    `cli/badge.py`; tests patch `subprocess.run`.

- **C-7 (observability) — `--verbose` flag.** Default silent-on-
  success, always-print-errors; add `--verbose` to opt into
  "wrote <path>" style info lines? Or ship without `--verbose` in
  v1 and add later if requested?

- **C-8 (testing) — Schema-drift guard.** Use a checked-in fixture
  `tests/fixtures/grading.json` (or generate via `GradingReport(
  ...).to_json()`) consumed by `tests/test_badge.py` to catch future
  `GradingReport.to_json` shape changes. Also applies to
  `assertions.json`.

- **C-9 (data model) — 0/0 L1 message format.** What does the
  `message` field read when `N/M` is `0/0`? Options:
  - **A.** `"no data"` (treat lightgrey uniformly).
  - **B.** `"0/0"` (literal).
  - **C.** `"no assertions"`.

- **C-10 (observability) — Stderr warnings for placeholder cases.**
  When writing the lightgrey "no iteration" badge or falling back to
  the `USER/REPO/main` placeholder in `--url-only`, should stderr
  emit a one-line warning so users notice? (Quiet-by-default vs.
  loud-on-uncertainty.)

- **C-11 (security) — `--output` parent-dir validation.** Allowing
  absolute paths means a typo could land on `/etc/passwd`. Mitigate
  with a `Path(output).parent.resolve(strict=False).is_dir()` check
  that fails exit 2 when the parent is a file/socket/symlink-to-
  dir-that-doesn't-exist. `mkdir(parents=True, exist_ok=True)` for
  the default `.clauditor/badges/` case.

- **C-12 (security) — `--style` URL encoding.** Values flow into the
  badge JSON (shields.io reads them from the JSON body, not the
  badge URL), so URL-encoding is NOT the right protection — string
  validation (reject control chars, reject values > N chars) is.
  Needs clarification on exactly how shields.io consumes these
  fields from the endpoint JSON.

---

## Refinement Log

### Decisions

**DEC-001. Lightgrey placeholder on "no iteration found".** When
`clauditor badge <skill>` runs for a skill with no discoverable
iteration, write a `lightgrey` "no data" badge JSON and exit 0.
Rationale: CI pipelines that always run `clauditor badge` need a
persistent placeholder rather than a transient error. (Q1=B.)

**DEC-002. Hybrid `--url-only` URL construction.** Auto-detect via
`git remote get-url origin` + default-branch detection; accept
`--repo USER/REPO` and `--branch NAME` explicit overrides; fall back
to `USER/REPO/main` placeholder when auto-detection fails. (Q2=D.)

**DEC-003. Variance layer graceful-degrade.** Include the
`clauditor.layers.variance` block when `variance.json` sidecar is
present; omit the block entirely when absent. Today's steady state
is "always absent" (no writer ships yet); future variance-writer
ticket lands additively. (Q3=A.)

**DEC-004. L3 thresholds read from `grading.json` sidecar.** The
`thresholds` block already persisted in `grading.json` (written by
`GradingReport.to_json`) is the source of truth for the badge's L3
`passed` field. No CLI override, no re-interpretation against a
possibly-evolved `EvalSpec.grade_thresholds`. (Q4=A.)

**DEC-005. `--output PATH` accepts absolute paths.** Common use is
writing the badge JSON into a GitHub Pages dir outside the repo.
Absolute path is a supported user request, not a spec-driven path,
so the `path-validation.md` restrictions don't apply. (Q5a=A.)

**DEC-006. Default output at `.clauditor/badges/<skill>.json`.**
Project-root-relative, outside the iteration tree. Stable URL,
persistent across iterations. (Q5b=A.)

**DEC-007. Zero L1 assertions → `lightgrey`.** A spec with no L1
assertions has no L1 signal to surface; lightgrey is clearer than
faking a trivially-green 0/0 pass. (Q6a=A.)

**DEC-008. `assertions.json` missing from an otherwise-present
iteration → exit 1.** Distinct from DEC-001. DEC-001 handles "no
iteration at all" (a valid state — no grading has run yet). A
present iteration with missing `assertions.json` is a corrupted
iteration; bail loudly. (Q6b=A.)

**DEC-009. L3 all parse-failed → `red`.** A grading run that
produces no scorable results is a failure signal and deserves a red
badge rather than silent omission. (Q6c=A.)

**DEC-010. Use `passed` (not `all_passed`) for both L1 and L3
layers; document semantic difference in dataclass docstrings.** L1
`passed = true` means "every assertion passed"; L3 `passed = true`
means "pass rate ≥ min_pass_rate AND mean_score ≥ min_mean_score"
(i.e., the grade met its thresholds). Shields.io extension stays
concise and avoids two names for "did this layer succeed". (BLK-1=A.)

**DEC-011. `--force` required to overwrite existing badge JSON.**
Matches `propose_eval` / `init` convention. If
`.clauditor/badges/<skill>.json` (or a custom `--output`) exists and
`--force` was not passed, exit 1 with a stderr error.
**Exception:** The DEC-001 lightgrey placeholder write does NOT
overwrite an existing badge without `--force` — a stale "real"
badge is less bad than silently clobbering it on a misfire. (BLK-2=A.)

**DEC-012. `generated_at` uses `Z` suffix form
(`2026-04-21T14:00:00Z`).** Python's
`datetime.now(timezone.utc).isoformat()` produces `+00:00`;
post-process with `.replace("+00:00", "Z")` at the single
serialization seam. (C-1=A.)

**DEC-013. Nested dataclasses matching `Benchmark` idiom.** Define
`L1Summary`, `L3Summary`, `VarianceSummary`, `ClauditorExtension`,
and `Badge` dataclasses with `to_endpoint_json()` methods that emit
the shields.io-compatible dict. Raw-dict fields only where
passthrough (e.g., `thresholds` block copied verbatim from
`grading.json`). (C-2=A.)

**DEC-014. `--url-only` and `--output` are mutually exclusive; both
passed → exit 2.** Both flags define conflicting outputs; silent
precedence is a trap. (C-3=A.)

**DEC-015. `--style KEY=VALUE` whitelist + stderr warning on unknown
key.** Whitelist = `style`, `logoSvg`, `logoColor`, `labelColor`,
`cacheSeconds`, `link` (per shields.io docs). Unknown keys warn to
stderr (`"clauditor.badge: unknown --style key 'foo' — passing
through anyway"`) but are still emitted in the JSON; shields.io
ignores unknowns so the badge still renders. Non-fatal, exit 0.
(C-4=A.)

**DEC-016. `--from-iteration N` with N not found → exit 1.**
Distinct from DEC-001. An explicit iteration request that fails is
an explicit error; stderr message names N and the available
iteration numbers. (C-5=A.)

**DEC-017. Extract `src/clauditor/_git.py` wrapper.** Two pure
helpers: `get_repo_slug(cwd: Path) -> str | None` (returns
`"USER/REPO"` parsed from the origin URL; handles HTTPS, SSH, and
custom git hosts) and `get_default_branch(cwd: Path) -> str | None`
(returns the branch name from `git symbolic-ref
refs/remotes/origin/HEAD`, or `None` on failure). Both wrap
`subprocess.run` with `FileNotFoundError` + `CalledProcessError`
handling; both return `None` instead of raising. CLI translates
`None` → placeholder fallback. Tests patch the wrapper functions,
not `subprocess.run` directly. (C-6=A.)

**DEC-018. `--verbose` flag, silent-by-default.** Default silent on
success; errors always print to stderr regardless of flag.
`--verbose` opts into info lines like `"wrote
.clauditor/badges/review-pr.json"`. (C-7=A.)

**DEC-019. Schema-drift fixtures generated in-test via
`to_json()`.** `tests/test_badge.py` factories mirror
`tests/test_benchmark.py` / `tests/test_baseline.py` — hand-author
`GradingReport`, `AssertionSet`, etc. via test helpers, serialize
with `to_json()`, parse back, pass to `compute_badge`. No fixture
dir created. (C-8=B.)

**DEC-020. 0/0 L1 message format is `"no data"`.** Applies to both
DEC-001 (no iteration) and DEC-007 (iteration exists but zero L1
assertions). Consistent lightgrey + "no data" string across both
"no signal" cases. (C-9=A.)

**DEC-021. Stderr warnings on lightgrey placeholder + git fallback.**
- Writing a DEC-001 lightgrey placeholder → stderr `"warning: no
  iteration found for skill {name} — wrote lightgrey placeholder
  (run 'clauditor grade' to populate)"`.
- Writing a DEC-007 lightgrey placeholder → stderr `"warning:
  eval spec declares 0 L1 assertions — wrote lightgrey 'no data'
  badge"`.
- `--url-only` with git auto-detect failure → stderr `"warning:
  git auto-detect failed; using placeholder USER/REPO/main — pass
  --repo USER/REPO to override"`.
Loud-on-uncertainty is the right default for a command whose
output is visible to everyone reading the user's README. (C-10=A.)

**DEC-022. `--output` parent-dir validation.** Resolve
`Path(output).parent` with `strict=False`, then check `.is_dir()`.
If parent does not exist or is not a directory, exit 2. Default
`.clauditor/badges/` is created via `mkdir(parents=True,
exist_ok=True)` — only a user-provided `--output` is validated.
(C-11=A.)

**DEC-023. `--style` value validation.** Each value: reject control
characters (`\x00-\x1f`, `\x7f`) via `str.isprintable()` or
equivalent; enforce ≤512 chars. Bad value → exit 2 with stderr
naming the offending key. Keys use the same character class as
shields.io's documented keys (already safe). (C-12=A.)

**DEC-024. Message format.** Per ticket table:
- L1 only: `"{passed}/{total}"` (e.g. `"8/8"`)
- L1 + L3 present: `"{N}/{M} · L3 {pct}%"` (percent rounded via
  `round(pass_rate * 100)`)
- L1 + L3 + variance present: `"{N}/{M} · L3 {pct}% · {stability}%
  stable"`.
- Zero L1 case (DEC-020): `"no data"`.

**DEC-025. Exit-code taxonomy: 0 / 1 / 2 (no exit 3).** Badge makes
no LLM call; the four-exit-code taxonomy from
`.claude/rules/llm-cli-exit-code-taxonomy.md` does not apply. Per
the taxonomy rule's own "When this rule does NOT apply" section,
non-LLM commands use 0/1/2.
- **0** — success (badge written or URL printed; DEC-001/-007
  lightgrey placeholder writes also return 0).
- **1** — runtime failure: corrupt iteration (DEC-008), existing
  file without `--force` (DEC-011), missing `--from-iteration N`
  (DEC-016), disk I/O errors.
- **2** — input-validation failure: bad skill spec, mutually
  exclusive flags (DEC-014), `--output` parent-dir check failure
  (DEC-022), `--style` value rejected (DEC-023).

**DEC-026. Pure compute vs. I/O split.** `src/clauditor/badge.py`
is pure (no stderr, no file I/O, no subprocess); takes pre-parsed
dicts and known identifiers (skill_name, iteration, generated_at).
`src/clauditor/cli/badge.py` owns sidecar reads, git subprocess
calls, output writes, stderr progress lines, and exit-code mapping.
Per `.claude/rules/pure-compute-vs-io-split.md`.

**DEC-027. Schema version layering.** Top-level `schemaVersion: 1`
is shields.io's schema (camelCase per their docs). Nested
`clauditor.schema_version: 1` is our internal extension version,
first key of the `clauditor` block per
`.claude/rules/json-schema-version.md`. The two versions bump
independently; a future shields.io bump to `schemaVersion: 2` does
not force a `clauditor.schema_version` bump and vice versa. No
loader exists yet (we only write the badge in v1); when a future
`clauditor badge --read` or trend-audit consumer lands, it follows
`audit.py::_check_schema_version` shape, checking the
`clauditor.schema_version` field (not the shields.io one, which is
THEIR contract).

### Session notes

- Session 1 (2026-04-21): Discovery → architecture → refinement
  completed in one sitting. User accepted all proposed defaults in
  both scoping and refinement rounds. No blockers remained at end
  of Phase 2 after DEC-010 and DEC-011 resolved BLK-1 and BLK-2.
  Variance-writer absence confirmed — badge ships with the
  graceful-degrade branch that is always-taken today.

---

## Detailed Breakdown

Ordering follows the feature's natural architecture: pure compute
core first (US-001), supporting pure helpers (US-002, US-003),
then the CLI integration that composes them (US-004), then docs
(US-005), then Quality Gate (US-006) and Patterns & Memory
(US-007).

---

### US-001 — Pure `compute_badge` + `Badge` dataclass family

**Description.** Implement the pure aggregation core in a new
`src/clauditor/badge.py` module. Takes pre-parsed sidecar dicts and
identity fields, returns a `Badge` dataclass whose
`to_endpoint_json()` method emits the shields.io-compatible dict.
No I/O; no stderr; no subprocess.

**Traces to:** DEC-003, DEC-009, DEC-010, DEC-012, DEC-013,
DEC-020, DEC-024, DEC-026, DEC-027.

**Files:**
- **New** `src/clauditor/badge.py` — nested dataclasses
  (`L1Summary`, `L3Summary`, `VarianceSummary`,
  `ClauditorExtension`, `Badge`), `compute_badge(...)` function,
  color-logic table as a module-level constant, message-format
  helper, `Badge.to_endpoint_json() -> dict`.
- **New** `tests/test_badge.py` — `TestComputeBadge` with
  parametrized color-logic table + per-branch message-format tests;
  `TestBadgeSerialization` for round-trip + schema_version
  first-key invariants.
- **No change** to `src/clauditor/__init__.py` — badge is internal;
  only CLI imports it.

**Acceptance criteria:**
- `compute_badge(assertions, grading, variance, *, skill_name,
  iteration, generated_at)` returns a `Badge` dataclass. All three
  sidecar args accept `None`: `assertions=None` represents the
  DEC-001 / DEC-008 "no L1 signal" case; `grading=None` omits L3;
  `variance=None` omits variance.
- Color logic (DEC-003, 007, 009, 020): L1 assertions count == 0
  → `lightgrey` + message `"no data"`; any L1 failed → `red`; L1
  all-pass + L3 below thresholds → `yellow`; L1 all-pass + L3
  parse-failed (empty results OR all results have
  `passed=False` AND no score) → `red`; L1 all-pass + L3 passed /
  omitted → `brightgreen`.
- Message format (DEC-024): L1 only → `"{N}/{M}"`; L1+L3 →
  `"{N}/{M} · L3 {round(pass_rate*100)}%"`; L1+L3+variance →
  `"{N}/{M} · L3 {pct}% · {stab_pct}% stable"`; lightgrey →
  `"no data"`.
- `Badge.to_endpoint_json()` returns a dict with top-level keys in
  order: `schemaVersion`, `label`, `message`, `color`, then any
  `--style`-injected passthrough keys (alphabetical), then
  `clauditor`. Inside `clauditor`, first key is `schema_version:
  1` per `.claude/rules/json-schema-version.md`; subsequent:
  `skill_name`, `generated_at`, `iteration`, `layers`.
- `generated_at` uses `Z` suffix (DEC-012); test asserts the
  trailing character.
- L1 and L3 layer dicts both carry a `passed: bool` field (DEC-010)
  with docstring explaining the two semantic meanings.
- Variance block omitted when `variance is None` (DEC-003); test
  asserts `"variance" not in result["clauditor"]["layers"]`.
- `ruff check src/clauditor/badge.py tests/test_badge.py` passes.
- Coverage for `badge.py` ≥95% (it's pure logic; every branch
  should be hit).

**Done when:**
- `uv run ruff check src/ tests/` passes.
- `uv run pytest tests/test_badge.py --cov=clauditor.badge
  --cov-report=term-missing` passes with `badge.py` ≥95%.
- Entire-project coverage stays ≥80% (unchanged CLI + helpers
  still cover themselves).

**Depends on:** none.

**TDD:**
Write parametrized failing tests first, then implement.
Test cases (one `@pytest.mark.parametrize` table per group):
- Color + message matrix: (l1_passed, l1_total, l3_state, variance
  present) → (color, message). Include every row of the DEC color
  table.
- Schema-version first-key: `list(result.keys())[0] ==
  "schemaVersion"`; `list(result["clauditor"].keys())[0] ==
  "schema_version"`.
- `generated_at` Z-suffix: `result["clauditor"]["generated_at"].
  endswith("Z")`.
- L1 vs L3 `passed` semantic: L1 `passed` mirrors `all_passed`; L3
  `passed` mirrors the thresholds-based calculation on the input
  `grading` dict.
- Variance-present vs omitted.
- Thresholds pass-through from `grading["thresholds"]` dict.
- Empty thresholds block (grading has no `thresholds` key) →
  either include defaults or omit the key (pick one behavior; test
  it; document on the dataclass).

---

### US-002 — Git wrapper (`_git.py`)

**Description.** New private module `src/clauditor/_git.py` with
two pure helpers that wrap `subprocess.run` for git metadata
lookups needed by `clauditor badge --url-only`. Each helper
returns `None` instead of raising so the CLI can fall through to
the `USER/REPO/main` placeholder.

**Traces to:** DEC-002, DEC-017.

**Files:**
- **New** `src/clauditor/_git.py` — `get_repo_slug(cwd: Path) ->
  str | None` (parses `git remote get-url origin` output; handles
  `https://github.com/USER/REPO.git`,
  `git@github.com:USER/REPO.git`, and `https://gitlab.com/USER/REPO`
  shapes; strips trailing `.git`); `get_default_branch(cwd: Path)
  -> str | None` (parses `git symbolic-ref
  refs/remotes/origin/HEAD` output, returns the branch name).
- **New** `tests/test_git.py` — `TestGetRepoSlug` and
  `TestGetDefaultBranch` patching `subprocess.run` via
  `unittest.mock.patch`.

**Acceptance criteria:**
- `get_repo_slug` returns `"USER/REPO"` from any of:
  `https://github.com/USER/REPO.git`,
  `https://github.com/USER/REPO`, `git@github.com:USER/REPO.git`,
  `git@github.com:USER/REPO`, `https://gitlab.com/group/sub/REPO`.
- `get_repo_slug` returns `None` on: `FileNotFoundError` (git not
  installed), `CalledProcessError` with non-zero exit (no origin,
  not a git repo), parse failure on unknown URL shape.
- `get_default_branch` returns the branch name (e.g. `"main"`,
  `"master"`, `"dev"`) from parsed `refs/remotes/origin/HEAD`.
- `get_default_branch` returns `None` on the same error set as
  `get_repo_slug`.
- Both helpers accept `cwd: Path` and pass it as `cwd=str(cwd)` to
  `subprocess.run`.
- Neither helper raises under any of the documented error
  conditions.
- `ruff check` passes; coverage on `_git.py` ≥95%.

**Done when:**
- `uv run ruff check src/ tests/` passes.
- `uv run pytest tests/test_git.py` passes.

**Depends on:** none.

**TDD:**
Write failing `TestGetRepoSlug` cases for each URL shape + each
error branch before implementing. Same for `TestGetDefaultBranch`.

---

### US-003 — Sidecar discovery + URL builder (pure helpers)

**Description.** Extend `src/clauditor/badge.py` with two more pure
helpers: one that locates "the iteration to read for skill X"
given a project dir and optional explicit iteration number, and
one that builds the `--url-only` Markdown image line from pure
inputs. No I/O beyond reading files where required for iteration
sidecar loading (wrapped via `audit.py::_read_json`).

**Traces to:** DEC-001, DEC-002, DEC-006, DEC-008, DEC-016,
DEC-026.

**Files:**
- **Edit** `src/clauditor/badge.py` — add:
  - `discover_iteration(project_dir: Path, skill_name: str,
    explicit: int | None) -> tuple[int, Path] | None` —
    `explicit=None` returns the latest iteration that has a
    `<skill_name>/` subdir; `explicit=N` returns
    `(N, iteration-N/<skill>)` if it exists, else `None`.
    Distinguishes "no iteration at all" (DEC-001) from "explicit
    missing" (DEC-016) via caller inspection — helper returns
    `None` for both; caller branches on `explicit is not None`.
  - `load_iteration_sidecars(iteration_skill_dir: Path) ->
    IterationSidecars` — dataclass with `assertions: dict | None`,
    `grading: dict | None`, `variance: dict | None`,
    `assertions_missing: bool` (True when the dir exists but
    `assertions.json` does not — DEC-008 corrupt iteration).
    Uses `audit._read_json` for each file.
  - `build_markdown_image(*, skill_name: str, repo_slug: str,
    branch: str, output_relpath: str, label: str) -> str` —
    pure URL builder returning the `![label](https://img.shields.io/
    endpoint?url=https://raw.githubusercontent.com/{repo_slug}/
    {branch}/{output_relpath})` string. URL-encodes each component
    via `urllib.parse.quote` (path-safe).
- **Edit** `tests/test_badge.py` — add `TestDiscoverIteration`,
  `TestLoadIterationSidecars`, `TestBuildMarkdownImage` test
  classes.

**Acceptance criteria:**
- `discover_iteration(project_dir, "X", None)` walks
  `.clauditor/iteration-*/` via existing `audit._scan_iteration_
  dirs`, picks the highest N where `iteration-N/X/` exists,
  returns `(N, iteration-N/X)`.
- `discover_iteration(project_dir, "X", 42)` returns
  `(42, iteration-42/X)` if the dir exists, else `None`.
- `discover_iteration` returns `None` when no iteration contains
  `<skill_name>/`.
- `load_iteration_sidecars(path)` reads present files; each absent
  file → `None` for that attribute.
- `load_iteration_sidecars(path).assertions_missing` is `True` when
  the iteration's skill dir exists but `assertions.json` does not,
  `False` when both are absent (DEC-001) or both present.
- `build_markdown_image(...)` produces the exact Markdown image
  shape from the ticket example; URL encoding on `skill_name` /
  `output_relpath` / `repo_slug` / `branch` handles edge chars
  (though `SKILL_NAME_RE` prevents most).
- All three helpers have no stderr, no subprocess, no mutation of
  inputs.
- Coverage ≥95% on the new symbols.

**Done when:**
- `uv run ruff check src/ tests/` passes.
- `uv run pytest tests/test_badge.py` passes.

**Depends on:** US-001 (shares the `badge.py` module and reuses
dataclass imports).

**TDD:**
Failing tests first. `TestDiscoverIteration` uses `tmp_path` to
construct fake iteration dirs. `TestLoadIterationSidecars` uses
`tmp_path` with hand-written JSON fixtures (assertions present /
grading only / all absent / assertions_missing case).
`TestBuildMarkdownImage` is pure-string assertions with
parametrized input combinations including edge-char inputs to
prove URL encoding.

---

### US-004 — CLI command + dispatcher wiring

**Description.** New `src/clauditor/cli/badge.py` exposing
`add_parser` + `cmd_badge`; wire into `cli/__init__.py::main`.
Composes US-001 / US-002 / US-003 helpers with argparse, sidecar
I/O, git subprocess calls, file writes, stderr progress, and
exit-code mapping.

**Traces to:** DEC-001, DEC-002, DEC-005, DEC-006, DEC-011,
DEC-014, DEC-015, DEC-016, DEC-018, DEC-021, DEC-022, DEC-023,
DEC-025, DEC-026.

**Files:**
- **New** `src/clauditor/cli/badge.py` — `add_parser`, `cmd_badge`,
  `_parse_style_arg(raw: str) -> tuple[str, str]`,
  `_validate_style_value(value: str) -> None`, any other small
  CLI-local helpers.
- **Edit** `src/clauditor/cli/__init__.py` — add lazy import of
  `cli.badge`, re-export `cmd_badge`, register `add_parser` in
  `main()`, add dispatch branch.
- **New** `tests/test_cli_badge.py` — CLI integration tests via
  `tmp_path`, `capsys`, and `unittest.mock.patch` on
  `clauditor._git.get_repo_slug`, `clauditor._git.get_default_branch`.

**Acceptance criteria:**
- Positional `skill` arg loads via `SkillSpec.from_file`
  (preserves frontmatter-first skill-name derivation — per
  `.claude/rules/skill-identity-from-frontmatter.md`).
- Flags: `--from-iteration N`, `--output PATH`, `--url-only`,
  `--force`, `--repo USER/REPO`, `--branch NAME`, `--label TEXT`
  (default `"clauditor"`), `--style KEY=VALUE` (append), `--verbose`.
- `--url-only` + `--output` both present → exit 2 with stderr
  `"ERROR: --url-only and --output are mutually exclusive"` (DEC-014).
- `--output` absolute path accepted (DEC-005); parent-dir check:
  `Path(output).parent.resolve(strict=False).is_dir()` must be
  true → else exit 2 (DEC-022).
- `--style` parsed via `raw.split("=", 1)`; key whitelist check
  (DEC-015 allowed set); unknown key → stderr warning but key
  still lands in the JSON; each value validated via DEC-023 rules
  → bad value exits 2.
- No `--from-iteration`, no iteration found for skill → write
  `lightgrey` "no data" badge (via `compute_badge(assertions=None,
  ...)`) at default or `--output` path; DEC-021 stderr warning;
  exit 0 (DEC-001).
- `--from-iteration N`, iteration N missing → exit 1 with stderr
  naming N and available iteration numbers (DEC-016).
- Present iteration with `assertions.json` missing (but skill-dir
  exists) → exit 1 (DEC-008).
- Output file already exists at the target path, no `--force` →
  exit 1 `"ERROR: {path} already exists (pass --force to
  overwrite)"` (DEC-011). Exception: DEC-001 lightgrey write also
  respects `--force` — do not clobber existing badge with
  placeholder unless `--force` was passed.
- `--url-only` mode: call `get_repo_slug` / `get_default_branch`
  unless `--repo` / `--branch` provided; on failure fall back to
  `"USER/REPO"` / `"main"` + DEC-021 stderr warning. Print the
  Markdown image line to stdout; do NOT write JSON; exit 0.
- `--verbose` + successful write → stderr info line
  `"clauditor.badge: wrote {path} (iteration {N})"` (DEC-018).
- Dispatcher in `cli/__init__.py` adds `elif parsed.command ==
  "badge": return cmd_badge(parsed)`.

**Done when:**
- `uv run ruff check src/ tests/` passes.
- `uv run pytest tests/test_cli_badge.py` passes with ≥90%
  coverage on `cli/badge.py`.
- `clauditor badge --help` renders the expected flag surface.

**Depends on:** US-001, US-002, US-003.

**TDD:**
Moderate. CLI tests are integration-heavy; write failing
parametrized tests for each exit-code branch (0 success, 0
lightgrey placeholder, 1 corrupt iteration, 1 explicit-missing
iteration, 1 overwrite-without-force, 2 mutual exclusion, 2
bad `--output` parent, 2 bad `--style` value) before wiring. Git
auto-detect branches: happy path (mock returns slug + branch),
slug-missing (mock returns None), branch-missing, both-missing,
explicit `--repo`/`--branch` overrides.

---

### US-005 — Docs: `docs/cli-reference.md#badge` + `docs/badges.md`

**Description.** Add a `## badge` subsection to
`docs/cli-reference.md` (flag-by-flag reference) and a new
`docs/badges.md` doc covering placement tradeoffs (README vs
SKILL.md vs catalog page) per the ticket's "Can badges go on
SKILL.md files?" discussion. If the new docs push the root README
past its teaser budget, apply the `readme-promotion-recipe` to
anchor a D2 lean teaser from the README.

**Traces to:** Ticket "Suggested breakdown" item 3; rule anchor
`.claude/rules/readme-promotion-recipe.md`.

**Files:**
- **Edit** `docs/cli-reference.md` — add `## badge` subsection
  matching the shape of `## propose-eval` and `## suggest`
  (description, synopsis, flag table, example session, exit
  codes).
- **New** `docs/badges.md` — opens with the breadcrumb blockquote
  (`> Returning from the [root README](../README.md). …`), then
  sections: "Why badges", "Placement hierarchy (README primary;
  catalog-page secondary; SKILL.md tradeoffs)", "Color logic
  table", "Embedding recipe (`--url-only`)", "CI integration"
  (placeholder stub — defer real GitHub Action to future ticket).
- **Edit** `README.md` — if the badge feature warrants a teaser,
  add a D2 lean teaser in the appropriate section per
  `readme-promotion-recipe.md`. If not (reference-only), skip.

**Acceptance criteria:**
- `docs/cli-reference.md` has a `## badge` anchor; all flags
  documented with the same phrasing pattern as sibling
  subsections.
- `docs/badges.md` opens with the breadcrumb blockquote.
- `docs/badges.md` color-logic table matches the ticket's table +
  the DEC-007/-009/-020 additions (zero L1 → lightgrey + "no
  data"; L3 parse-failed → red).
- Example sessions copy-paste cleanly (matches the ticket's
  `clauditor badge src/skills/review-pr/SKILL.md --url-only`
  example).
- No broken internal links (spot-check
  `../README.md`, `./cli-reference.md#badge`, rule-anchor links
  if used).

**Done when:**
- Lint passes (no doc linter in the project, but spot-check Markdown
  renders on GitHub preview).
- Manual review of the new doc confirms the placement hierarchy
  reads as the ticket described it.

**Depends on:** US-004 (CLI behavior must be stable before docs
freeze the flag surface).

**TDD:** N/A for docs.

---

### US-006 — Quality Gate (code review x4 + CodeRabbit)

**Description.** Full changeset quality gate before merge. Run
the `code-review` skill four times across the entire diff, fixing
every real finding each pass. Run CodeRabbit on the PR and
reconcile. Project validation (`uv run ruff check src/ tests/`
+ `uv run pytest --cov=clauditor --cov-report=term-missing` with
the 80% gate) must pass at the end.

**Traces to:** All DECs (invariant verification).

**Files:** all changed files from US-001 through US-005 (exact
list from `git diff --name-only <base>`).

**Acceptance criteria:**
- 4 passes of code review; every real bug fixed, every concern
  either resolved or deferred with a new beads issue.
- CodeRabbit comments triaged: each comment either fixed or
  documented as false-positive with a short note (per the
  `pr-reviewer` agent's contract).
- `uv run ruff check src/ tests/` passes.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes
  with overall coverage ≥80%.

**Done when:**
- Last code-review pass returns zero real findings.
- CodeRabbit PR comments all addressed or documented.
- CI (if wired) green.

**Depends on:** US-001, US-002, US-003, US-004, US-005.

**TDD:** N/A.

---

### US-007 — Patterns & Memory (update conventions + docs)

**Description.** Capture any new patterns learned in this epic
into the `.claude/rules/` directory or project memory, and update
existing rules if a decision here extended them. Always-last
story per the `/super-plan` contract.

**Candidate patterns:**

- **Rule: dual-version JSON payloads.** The badge JSON carries
  shields.io's `schemaVersion` (THEIR contract) + our
  `clauditor.schema_version` (OUR contract). Extending
  `.claude/rules/json-schema-version.md` (or adding a sibling
  rule) to cover "external-schema + embedded-extension" shape
  would codify this for future commands. Decision point: add the
  rule if a second command needs dual-version shaping; otherwise
  document in the `badge.py` module docstring and skip the rule
  file.
- **Rule: git-metadata wrapper.** `src/clauditor/_git.py` is a
  new canonical anchor. If a second command later needs git
  metadata, promote `_git.py` + the patch-the-wrapper test
  pattern into a short `.claude/rules/git-subprocess-wrapper.md`.
- **Rule: placeholder-on-no-data CLI pattern.** DEC-001's "write
  a lightgrey placeholder + exit 0" is a new CLI pattern
  (distinct from the exit-2 "bad input" and exit-1 "bad state"
  branches). If a future CLI command emits placeholders for
  similar reasons, document the pattern.
- **Rule: dual-lightgrey source collision.** DEC-001 (no
  iteration) and DEC-007 (zero L1 assertions) both produce
  lightgrey with the same `"no data"` message. If this causes
  downstream confusion (e.g. a future audit consumer can't tell
  them apart from the badge alone), add a differentiating field
  inside the `clauditor` extension block.

**Files:**
- **New or edit** `.claude/rules/<name>.md` — only if one of the
  candidate patterns above materializes into enough repetition
  across the codebase to justify a rule. Otherwise skip.
- **Edit** the user-level memory at
  `~/.claude/projects/-home-wesd-Projects-clauditor/memory/` only
  if a genuinely cross-conversation fact emerged (unlikely for
  this epic).

**Acceptance criteria:**
- At least one short review pass: "Did this epic introduce a
  pattern that recurs elsewhere in the codebase?" — answer
  recorded as a commit message note even if the answer is "no new
  rule needed".
- If a rule file was added or edited, it is referenced from the
  driving code comment (so future greppers find it).

**Done when:**
- Committed (and pushed) per session-close protocol.

**Depends on:** US-006.

**TDD:** N/A.

---

### Dependency graph summary

```
US-001 (pure compute) ──┐
US-002 (_git.py)     ───┤
US-003 (sidecar + URL) ─┘→ US-004 (CLI) → US-005 (docs) → US-006 (QG) → US-007 (P&M)
```

US-001, US-002, and US-003 can be worked in any order (US-003
imports `badge.py` symbols, so logically after US-001 lands
first, but the stories are independent enough to parallelize if
two workers pick them up). US-004 blocks on all three. US-005
blocks on US-004 for flag stability.

### Rules compliance gate (pre-Phase-5 check)

- ✅ `json-schema-version.md` — DEC-027; `clauditor.schema_version:
  1` as first key of the `clauditor` block (US-001).
- ✅ `pure-compute-vs-io-split.md` — DEC-026; US-001 pure + US-004
  I/O.
- ✅ `llm-cli-exit-code-taxonomy.md` — DEC-025; non-LLM command
  uses 0/1/2 (rule's own "does not apply" clause).
- ✅ `path-validation.md` — DEC-022 parent-dir check (US-004).
- ✅ `in-memory-dict-loader-path.md` — N/A (sidecars are
  clauditor-written, not LLM-proposed; direct `json.load` is
  appropriate).
- ✅ `constant-with-type-info.md` — N/A at v1; color-logic table
  is a flat string-valued constant with no mixed-type payload.
- ✅ `skill-identity-from-frontmatter.md` — US-004 loads skill via
  `SkillSpec.from_file`.
- ✅ `project-root-home-exclusion.md` — N/A (explicit skill path
  argument; no marker walk).
- ✅ `sidecar-during-staging.md` — N/A (reads finalized
  iterations; writes outside the iteration tree).
- ✅ `readme-promotion-recipe.md` — US-005 docs plan.
- ✅ `bundled-skill-docs-sync.md` — N/A (bundled `/clauditor`
  SKILL.md workflow does not invoke badge in v1).
- ✅ `non-mutating-scrub.md` — N/A (no redaction path in v1).
- ✅ `monotonic-time-indirection.md` — N/A (no async / no
  duration-tracking).
- ✅ `centralized-sdk-call.md` — N/A (no Anthropic calls).
- ✅ `eval-spec-stable-ids.md` — N/A (no new EvalSpec fields).

---

## Beads Manifest

- **Epic:** `clauditor-wnv` — #77: clauditor badge command (shields.io endpoint JSON)
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/77-clauditor-badge`
- **PR:** https://github.com/wjduenow/clauditor/pull/81

### Tasks

| Bead | Title | Deps |
|---|---|---|
| `clauditor-wnv.1` | US-001 — Pure compute_badge + Badge dataclass family | none |
| `clauditor-wnv.2` | US-002 — Git wrapper (_git.py) for repo-slug + default-branch | none |
| `clauditor-wnv.3` | US-003 — Sidecar discovery + URL builder (pure helpers) | wnv.1 |
| `clauditor-wnv.4` | US-004 — CLI command cli/badge.py + dispatcher wiring | wnv.1, wnv.2, wnv.3 |
| `clauditor-wnv.5` | US-005 — Docs: cli-reference.md#badge + new docs/badges.md | wnv.4 |
| `clauditor-wnv.6` | US-006 — Quality Gate: code-review x4 + CodeRabbit | wnv.1, wnv.2, wnv.3, wnv.4, wnv.5 |
| `clauditor-wnv.7` | US-007 — Patterns & Memory: update conventions + docs | wnv.6 |

### Ready-to-work (no blockers)

- `clauditor-wnv.1` — US-001 pure compute_badge
- `clauditor-wnv.2` — US-002 git wrapper

Run `bd ready` in the worktree to confirm.
