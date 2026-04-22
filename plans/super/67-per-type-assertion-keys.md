# Super Plan: #67 — Redesign assertion schema with per-type semantic keys

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/67
- **Branch:** `feature/67-per-type-assertion-keys`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/67-per-type-assertion-keys`
- **Phase:** `done`
- **PR:** https://github.com/wjduenow/clauditor/pull/69
- **Sessions:** 1
- **Last session:** 2026-04-20

---

## Discovery

### Ticket Summary

**What:** The `value` slot on `EvalSpec` assertions is semantically
overloaded — one key, five distinct meanings depending on assertion
`type`:
- string **needle** for `contains` / `not_contains`
- string **pattern** for `regex`
- int **length threshold** for `min_length` / `max_length`
- int **minimum count** for `has_urls` / `has_entries` /
  `urls_reachable`
- string **pattern** for `min_count` (with separate `minimum` field
  holding the actual count)

Proposal: replace `value` with per-type semantic keys that name what
they hold (`needle`, `pattern`, `length`, `count`, `min_count`,
`format`). An LLM proposer (or hand author) reading the schema
can't accidentally emit `min` for `min_length` because the expected
key is `length` — and the hard-validator landed in #61 rejects
mismatches loudly at load time.

**Why:** The overloaded `value` is what invited the propose-eval
proposer in #61 to reach for `pattern`/`min`/`max` — the LLM's
guesses were semantically correct, the schema just happened to call
all of them `value`. Fixing the schema eliminates the class of
ambiguity that caused #61's silent false-positive bug in the first
place. The #61 hard-validator (`_require_assertion_keys`) is now
the safety net that makes this redesign shippable without silent-
regression risk between now and landing.

**Done when:**
1. Every assertion type's parameter is read from a semantic
   per-type key, not `value`.
2. The per-type `_require_assertion_keys` validator rejects legacy
   `value`-shape specs at load time with an actionable "did you
   mean …?" hint (DEC-003 deferred `schema_version` on EvalSpec;
   the hard-validator is the safety net).
3. All in-repo `*.eval.json` files migrated to the new shape and
   loadable without warnings.
4. All in-repo test fixtures use the new keys.
5. `docs/eval-spec-reference.md`, `README.md` examples, bundled
   `clauditor.eval.json` rubric, `cli/init.py` scaffolding, and
   `propose_eval.py` prompt all reflect the new keys.
6. `ASSERTION_TYPE_REQUIRED_KEYS` + `AssertionKeySpec` updated so
   the validator, proposer prompt, and tests all agree on the new
   key set.

**Who benefits:** Every future author (hand or LLM) of an eval
spec — intuitive key names prevent the next wave of "I guessed
`pattern` but the schema says `value`" errors. Also: every
maintainer reading an existing spec, who no longer has to remember
which of five meanings `value` holds in context.

### Codebase Findings

**Schema infrastructure (the gift from #61):**
- `src/clauditor/schemas.py:14-32` — `AssertionKeySpec` frozen
  dataclass with `required: frozenset[str]` and `optional:
  frozenset[str]`.
- `src/clauditor/schemas.py:47-73` — `ASSERTION_TYPE_REQUIRED_KEYS`
  dict, single source of truth for 10 types' required/optional
  keys. The proposer prompt, loader validator, and cross-check
  tests all import this constant. **This is exactly the seam #67
  modifies — change the key names in the dict, everything
  downstream picks them up automatically.**
- `src/clauditor/schemas.py:345-391` — `_require_assertion_keys`
  nested in `EvalSpec.from_dict`. Rejects unknown keys; emits
  drift-alias hints (`pattern`→`value`, `min`→`value`,
  `max`→`value`, `threshold`→`minimum`). These drift-hints will
  be rewritten to reflect the new keys.
- `src/clauditor/propose_eval.py` — the proposer prompt renders
  a per-type enumeration table from
  `ASSERTION_TYPE_REQUIRED_KEYS`. **Rebuilds automatically when
  the constant changes.**

**Current `value` reader — the 10 handlers:**

| Type | Extraction | `value` semantics today | Proposed key(s) |
|---|---|---|---|
| `contains` | `a.get("value", "")` | string needle | `needle` |
| `not_contains` | `a.get("value", "")` | string needle | `needle` |
| `regex` | `a.get("value", "")` | regex pattern | `pattern` |
| `min_count` | `a.get("value", "")` + `a.get("minimum", 1)` | pattern + count | `pattern` + `count` |
| `min_length` | `int(a.get("value", ""))` | length int | `length` |
| `max_length` | `int(a.get("value", ""))` | length int | `length` |
| `has_urls` | `int(a.get("value", "")) or 1` | optional min count | `min_count` (optional, default 1) |
| `has_entries` | `int(a.get("value", "")) or 1` | optional min count | `min_count` (optional, default 1) |
| `urls_reachable` | `int(a.get("value", "")) or 1` | optional min count | `min_count` (optional, default 1) |
| `has_format` | `a.get("format", "")` + `int(a.get("value","")) or 1` | format name + optional min count | `format` + `min_count` |

Note the dual-pattern use: `regex` AND `min_count` both want a
regex-string key. The ticket proposes `pattern` for both, which is
shared-key naming (same semantic, same key name) — reconciled in
DEC-001 below.

**Stringly-typed ints on disk:** checked-in specs store counts and
lengths as JSON **strings** (e.g. `"value": "500"`, `"value": "3"`),
coerced at runtime via `int(a.get(...))`. A schema-version bump is
the natural moment to switch to native JSON ints.

**EvalSpec has NO `schema_version` today.** Grep of `schemas.py`:
zero matches. The ticket says "bump schema_version" but there is
nothing to bump — we introduce it fresh. The loader today accepts
unversioned JSON; all `schema_version` usage in the codebase is on
**output** sidecars (grading.json, benchmark.json, etc.), never on
the EvalSpec **input**.

**Migration surface — only 2 in-repo `*.eval.json` files:**
- `src/clauditor/skills/clauditor/assets/clauditor.eval.json` — 3
  assertions (`contains`×2, `min_length`×1).
- `examples/.claude/commands/example-skill.eval.json` — 8
  assertions across 6 types.

**Migration surface — test fixtures (hand-written dicts):**
~100+ `"value":` occurrences across 12 test files. Heavy hitters:
- `tests/test_schemas.py` (~35 inline assertion dicts)
- `tests/test_propose_eval.py` (~15)
- `tests/test_cli.py` (~10)
- `tests/test_assertions.py` (~20 inline dicts with `"value":`)
Plus scattered fixtures in `conftest.py`, `test_spec.py`,
`test_baseline.py`, `test_cli_transcript_slice.py`,
`test_cli_propose_eval.py`, `test_asserters.py`.

**Documentation surface:**
- `README.md:125` — one assertion example
- `docs/quick-start.md:20-23` — four inline examples
- `docs/eval-spec-reference.md:61-65` — five examples inside a
  complete spec
- `src/clauditor/cli/init.py:55-58` — scaffolding starter
  assertions (imported into every `clauditor init` output)
- `src/clauditor/assertions.py:463` — docstring mentions schema
- `SKILL.md` — no current `value` examples (safe)

### Applicable `.claude/rules/`

- **`json-schema-version.md`** — LOAD-BEARING. We must (a)
  introduce `schema_version` on EvalSpec as the first top-level
  key, and (b) have loaders verify it via hard numeric comparison
  with a skip+log on mismatch. Given this is the FIRST
  introduction of version on EvalSpec, DEC-003 below captures how
  to treat unversioned files.
- **`eval-spec-stable-ids.md`** — assertion `id` uniqueness is
  load-bearing for audit history. The key redesign does NOT
  change id validation; all migrations preserve existing ids
  verbatim.
- **`pre-llm-contract-hard-validate.md`** — the proposer prompt
  gets a new per-type enumeration table automatically (rendered
  from the updated constant); the hard-validator in `from_dict`
  inherits the new required-key sets.
- **`in-memory-dict-loader-path.md`** — `EvalSpec.from_dict(data,
  spec_dir=...)` is the path the proposer uses. Already compliant;
  the new validation rules land inside `from_dict` the same way
  #61's did.
- **`llm-cli-exit-code-taxonomy.md`** — if we add a migration CLI
  command, it follows the 0/1/2 taxonomy (no API call → no
  exit 3). Pure migration = 0 on success, 1 on write failure, 2
  on unrecognized input shape.
- **`pure-compute-vs-io-split.md`** — the migration logic (old-
  shape dict → new-shape dict) is a pure compute function.
  Wrapper scripts / CLI do I/O.
- **`readme-promotion-recipe.md`** — anchor text in `README.md`
  and `docs/eval-spec-reference.md` must stay byte-identical
  across edits (GitHub anchors).
- **`bundled-skill-docs-sync.md`** — NOT applicable. The SKILL.md
  `## Workflow` does not change. Only the bundled
  `clauditor.eval.json` rubric does, which is NOT the SKILL.md
  workflow.
- **`path-validation.md`** — NOT applicable. No new path-bearing
  fields; `input_files` validation is untouched.
- **`positional-id-zip-validation.md`** — NOT applicable. No new
  judge; assertion evaluation is per-entry.
- **`mock-side-effect-for-distinct-calls.md`** — if new tests mock
  a function called multiple times, use `side_effect=[...]`.
  Preventive; no current blocker.
- **`data-vs-asserter-split.md`** — NOT applicable. `Assertion`
  data-vs-asserter shape is untouched.
- **`centralized-sdk-call.md`** — NOT applicable. No new SDK
  usage.
- **`subprocess-cwd.md`** / **`monotonic-time-indirection.md`** /
  **`stream-json-schema.md`** / **`non-mutating-scrub.md`** — all
  N/A (no new subprocess, timing, streaming, or redaction
  surface).
- **`skill-identity-from-frontmatter.md`** /
  **`project-root-home-exclusion.md`** /
  **`pytester-inprocess-coverage-hazard.md`** — all N/A.

### Project validation commands

- Lint: `uv run ruff check src/ tests/`
- Test + coverage: `uv run pytest --cov=clauditor --cov-report=term-missing`
- Coverage gate: 80% (enforced).

### Ambiguities → Phase 1 scoping questions

Several key-naming and migration-scope choices in the ticket are
underdetermined. Questions below become DEC-### during refinement.

---

## Scoping Questions (Phase 1)

**Q1 (DEC-001) — Field-name reconciliation across the 10 types.**

The ticket proposes specific keys per type, but two naming
collisions need resolving:

- `pattern` is proposed for both `regex` and `min_count` (the
  type). Same semantic ("a regex string"), so sharing a key name
  is fine, but worth confirming.
- `count` is proposed as the field on `min_count`-the-type,
  while `min_count` is proposed as the field name on
  `has_urls` / `has_entries` / `urls_reachable` / `has_format`.
  That is, `min_count` is both a **type name** AND a **field name
  on other types**, and the threshold-int field is named `count`
  in one place and `min_count` in another.

Resolution options:

- **A)** **Accept ticket naming verbatim.** `regex` → `pattern`;
  `min_count` type → `pattern` + `count`; `min_length` /
  `max_length` → `length`; `has_urls` / `has_entries` /
  `urls_reachable` → `min_count`; `has_format` → `format` +
  `min_count`; `contains` / `not_contains` → `needle`. Accept the
  `count`/`min_count` naming asymmetry because each field name is
  read in context (inside a `min_count` type entry, a `count` key
  is unambiguous).
- **B)** **Unify on `count` everywhere.** `min_count`-the-type →
  `pattern` + `count`; `has_urls` / `has_entries` / etc. →
  `count`; `has_format` → `format` + `count`. Shorter, uniform,
  one fewer thing to remember. Field meaning still clear from
  context (`has_urls` with `count: 3` = "at least 3 URLs").
- **C)** **Unify on `min_count` everywhere.** `min_count`-the-
  type → `pattern` + `min_count`; `has_urls` / `has_entries` /
  etc. → `min_count`; `has_format` → `format` + `min_count`.
  Field name echoes the semantic ("minimum count") but now the
  type and field share a name on `min_count`-the-type entries —
  `{"type": "min_count", "pattern": "...", "min_count": 5}` —
  which is legal but awkward.
- **D)** **Rename `min_count`-the-type to remove the clash.**
  e.g. `pattern_min_count` or `regex_count` with field `count`;
  `has_urls` etc. use `min_count` as the field (uniform with
  `has_format`). Eliminates any shared-name ambiguity, but the
  type rename touches more surface (handlers, prompt, docs).

*Recommendation:* **B (unify on `count`).** Field meaning is clear
from context; uniform naming keeps the prompt-table and
documentation tight; avoids both collisions without renaming a
type. Explicit mapping per type below.

**Q2 (DEC-002) — Integer typing: accept JSON ints, strings, or
both?**

Today, counts/lengths are stored on disk as JSON strings
(`"value": "500"`) and coerced at runtime (`int(a.get("value"))`).
The schema-version bump is a natural moment to tighten this.

- **A)** **Accept JSON ints only.** `{"length": 500}` (int) is
  valid; `{"length": "500"}` (string) raises `ValueError` at load
  time with a helpful hint. Cleanest — native types for native
  data.
- **B)** **Accept both JSON ints and numeric strings.** Same
  permissive behavior as today, applied to the new keys. Lowest
  friction for hand authors.
- **C)** **Accept both, but emit a deprecation warning on string
  ints.** Accept both; warn to stderr when strings are used.
  Intermediate nudge.

*Recommendation:* **A (ints only).** Schema version 2 is the
right moment to tighten; pre-1.0 project; migration tooling can
bulk-fix the two in-repo specs. String-typed ints on disk have
no defender.

**Q3 (DEC-003) — `schema_version` introduction strategy.**

EvalSpec has no `schema_version` today. Introducing one is itself
a breaking change for loaders (anything that reads the JSON must
now check it), though the rule says tolerate mismatches with
skip+log, not hard-fail.

- **A)** **Introduce `schema_version: 1` in a prep PR/commit,
  THEN bump to 2 for the key redesign in a second step.**
  Cleaner history; two small diffs. Prep PR: add field, update
  writers, update loaders to expect it (v1), reject unknown
  versions with skip+log.
- **B)** **Introduce `schema_version: 2` directly as part of the
  redesign.** Single breaking change. Loader reads v2 with the
  new keys; unversioned files treated as v1 (legacy shape) and
  either (i) auto-migrated in-memory or (ii) rejected with a
  migration hint.
- **C)** **Skip `schema_version` entirely on EvalSpec.** Trust
  the per-type required-key validator from #61 to reject old-
  shape specs loudly (unknown key `value` → error with hint
  "did you mean `needle` / `pattern` / `length` / ..."). No
  version field, no version check.

*Recommendation:* **B** with a twist — introduce
`schema_version: 2` directly, but the loader's missing-version
branch treats the file as **v1 legacy** and emits a clear
`ValueError` pointing at the migration tool (per DEC-004). This
way, any hand-authored file with the old `value` keys gets an
explicit, on-topic error rather than a noisy cascade of "unknown
key `value`" errors from the per-type validator.

**Q4 (DEC-004) — Migration tooling: CLI command, one-shot
script, or none?**

Two in-repo `.eval.json` files + ~100 inline test dicts need
migrating. External users are few/none pre-1.0.

- **A)** **One-shot internal script** at
  `scripts/migrate_evalspec_v1_to_v2.py`. Converts old-shape
  dicts to new-shape in place for any target path. Used once
  by us to bulk-migrate in-repo files; thrown away after the
  ticket lands (or kept as a reference for external users).
- **B)** **Public `clauditor migrate-eval-spec <path>` CLI
  subcommand.** Discoverable by external users; matches the
  `clauditor ...` command idiom. Takes `--write` vs `--dry-run`
  flags. Follows the exit-code taxonomy (0 success / 1 write
  error / 2 unrecognized shape).
- **C)** **Both** — the public CLI uses the same pure helper as
  the internal bulk script.
- **D)** **No migration tool.** Hand-edit the two files; test
  fixtures are migrated as part of individual story diffs;
  external users follow the loader's error hints.

*Recommendation:* **C**. The pure migration function is ~30
lines; exposing it as a CLI command is cheap and means external
users with hand-authored specs get a first-class migration path.
Matches `pure-compute-vs-io-split.md` (pure helper, thin I/O
wrappers). Stories: the CLI command + migration helper is one
story; the internal bulk-migration application is another.

**Q5 (DEC-005) — Optional/default semantics for the new
`min_count` (or `count` per DEC-001) field on
`has_urls`/`has_entries`/`urls_reachable`/`has_format`.**

Today `value` is optional on these four types, defaulting to 1.

- **A)** **Preserve optional-with-default-1.** New key is
  optional; missing defaults to 1. Zero user-facing semantic
  change.
- **B)** **Make required.** Every assertion must explicitly
  state its threshold. Breaking, but enforces intent.
- **C)** **Preserve optional-with-default-1, but log a
  deprecation warning when omitted.** Intermediate nudge
  toward explicit thresholds.

*Recommendation:* **A**. Don't change two things at once.
Redesign the keys; leave semantics alone. A future ticket can
tighten to required-only if we see real-world usage trends
that warrant it.

**Q6 (DEC-006) — Test fixture migration shape.**

~100 inline assertion dicts across 12 test files. Options:

- **A)** **Hand-migrate every fixture.** Each test file's diff
  updates the key names. Straightforward.
- **B)** **Hand-migrate + add a pytest fixture factory**
  (e.g. `make_assertion(type, id, **kwargs) -> dict`) in
  `conftest.py`. New tests use the factory; existing tests
  migrate to use it opportunistically. Prevents future
  hand-construction drift.
- **C)** **Introduce a test-time shim that accepts old-shape
  dicts and silently rewrites to new shape.** Tests stay
  unchanged. Bad idea — tests should reflect the new schema,
  not hide it.

*Recommendation:* **A**. Pure hand-migration is honest and
auditable. Factory (option B) is a nice-to-have that can
follow in a Patterns & Memory story if the pattern's value
survives contact with the migration.

**Q7 (DEC-007) — Docs + README + bundled-rubric update
cadence.**

Breaking schema change requires doc updates in lockstep.

- **A)** **Everything in one PR.** README, docs/*.md, bundled
  `clauditor.eval.json`, `cli/init.py` scaffolding,
  `examples/.claude/commands/example-skill.eval.json`, and
  `propose_eval.py` prompt (automatic from the constant)
  all land together. Atomic, no divergence window.
- **B)** **Docs follow code.** Code lands first; docs PR is a
  follow-up. Short divergence window is tolerable for a
  pre-1.0 project.
- **C)** **Docs lead code.** Write docs first (showing the
  target shape), then implement. Forcing function.

*Recommendation:* **A** (atomic). Breaking schema changes that
touch docs should always be atomic — any divergence window is
exactly when readers hit "the docs say one thing, the code
says another" confusion. Each doc update is a small diff; no
reason to split.

**Q8 (DEC-008) — Back-compat window for unversioned / v1
specs.**

Once v2 lands, a user who clones clauditor + has an old
`*.eval.json` in their skill dir will hit the "unversioned or
v1" loader branch. What's the experience?

- **A)** **Hard-reject, point at migration tool.** Error:
  `"EvalSpec: unversioned or v1 schema detected in <path>.
  Run `clauditor migrate-eval-spec <path>` to update to v2."`
  No auto-migration; user must act explicitly. Clean.
- **B)** **Auto-migrate at load time (in-memory, not on
  disk).** Load succeeds silently; user never sees the error.
  Hidden magic; one class of future confusion ("why does the
  loaded spec not match my file?").
- **C)** **Auto-migrate and rewrite the file on disk.**
  Transparent but destructive (modifies user files without
  explicit consent).

*Recommendation:* **A**. Hard-reject with a clear next step.
Explicit migration is the right contract; this is what the
`schema_version` field is for.

---

## Scoping Answers (Session 1)

Revised in light of "no external users of clauditor":

- **DEC-001 (Q1=B) — Unify on `count`.** Per-type field mapping:
  - `contains` / `not_contains` → `needle` (required)
  - `regex` → `pattern` (required)
  - `min_count` (type) → `pattern` + `count` (both required)
  - `min_length` / `max_length` → `length` (required)
  - `has_urls` / `has_entries` / `urls_reachable` → `count` (optional, default 1)
  - `has_format` → `format` (required) + `count` (optional, default 1)
- **DEC-002 (Q2=A) — Native JSON ints only.** `{"length": 500}`
  valid; `{"length": "500"}` rejected at load with a helpful error.
  Migration updates the two in-repo specs to use native ints.
- **DEC-003 (Q3=C) — Skip `schema_version` on EvalSpec.**
  `.claude/rules/json-schema-version.md` anchors on clauditor's
  **output** sidecars; EvalSpec is user-authored **input**. With
  no external users, the per-type validator's "unknown key" error
  is sufficient to surface a stale-shape spec. Defer versioning
  until a real-world need arises.
- **DEC-004 (Q4=D) — No migration tool.** Two in-repo specs
  hand-edit. Test fixtures migrate as part of each story's diff.
- **DEC-005 (Q5=A) — Preserve optional-with-default-1.** The
  `count` field on `has_urls` / `has_entries` / `urls_reachable` /
  `has_format` stays optional. Missing → default 1. No semantic
  change; redesign the keys only.
- **DEC-006 (Q6=A) — Hand-migrate test fixtures.** Per-story
  diffs update each test file. No test-time shim, no bulk
  rewriter.
- **DEC-007 (Q7=A) — Atomic single-PR.** Code + docs + README +
  bundled rubric + `cli/init.py` scaffolding all land together.

---

## Architecture Review

Scope is bounded (rename keys in one constant + flip handler
reads + update 2 specs + rewrite per-type drift-hints + update
~100 test fixtures + docs). Review areas that are trivially
`pass` for this shape are compressed into one-line findings; the
two concerns drive Phase 3 decisions.

| Area | Rating | Finding |
|---|---|---|
| Security | pass | No new inputs. Proposer prompt's per-type table re-renders from the updated constant; untrusted-content framing (`llm-judge-prompt-injection.md`) is unchanged. |
| Performance | pass | Loader is still O(n) over assertions. `int(a.get("value", ""))` runtime coercion in handlers goes away when JSON ints replace stringly-typed ints — trivial speedup, noise-level. No hot-path reshape. |
| Data Model | pass | Persisted sidecars (`grading.json`, `assertions.json`, `baseline_*.json`, benchmark) serialize `AssertionResult` (id + status + evidence + raw_data), NOT the assertion dict itself — so historical audit records remain readable after the rename. `EvalSpec.to_dict` passes `self.assertions` through verbatim, so after migration it emits new-shape dicts. No other caller serializes the spec shape. DEC-003 (skip `schema_version`) accepted as an internal-project risk. |
| API Design | pass | No CLI flag changes. Exit-code taxonomy unchanged. `clauditor init` scaffolds new-shape starter assertions but command surface is untouched. |
| Observability | pass | Validator error messages tighten via per-type drift-hints (see Drift-Hint Redesign concern). No new stderr lines; no new logging surface. |
| **Testing Strategy** | concern | Three sub-concerns: **(a)** existing #61 drift-hint tests assert literals like `"unknown key 'pattern' — did you mean 'value'?"` which become stale (in the new world, `pattern` is a valid key for `regex` and `min_count`-type); **(b)** the `test_handler_signature_agrees_with_constant` cross-check from #61 must continue to pass after handler lambdas are edited to read the new keys — handler-introspection regex may need updating; **(c)** prompt-builder tests that pin the literal row `"min_count → required: value · optional: minimum"` must be updated to the new rendering (e.g. `"min_count → required: count, pattern"`). |
| **Drift-Hint Redesign** | concern | The current `_require_assertion_keys` drift-hints are **globally** keyed (`pattern`/`min`/`max` → suggest `value`; `threshold` → suggest `minimum`). After the rename, `pattern` becomes a VALID key for two types, `minimum`/`threshold` are obsolete, and `value` itself is the canonical stale key every hand-author with muscle memory will type. The right shape is a **per-type drift-hint table** — for each type, a map from common-wrong-keys → correct-key-for-that-type. This is the single meaningful design task in the ticket. |

No blockers. Both concerns resolve as design decisions in the
Refinement Log (DEC-009, DEC-010).

---

## Refinement Log

### Decisions

- **DEC-009 — Per-type drift-hint table (Q9=A).**
  Introduce `_ASSERTION_DRIFT_HINTS: dict[str, dict[str, str]]`
  in `schemas.py` as a sibling constant to
  `ASSERTION_TYPE_REQUIRED_KEYS`. For each type, a map of
  `common-wrong-key → correct-key-for-this-type`. Concrete
  shape:
  ```python
  _ASSERTION_DRIFT_HINTS: dict[str, dict[str, str]] = {
      "contains":       {"value": "needle", "pattern": "needle"},
      "not_contains":   {"value": "needle", "pattern": "needle"},
      "regex":          {"value": "pattern"},
      "min_count":      {"value": "pattern", "minimum": "count",
                         "min_count": "count", "threshold": "count"},
      "min_length":     {"value": "length", "min": "length"},
      "max_length":     {"value": "length", "max": "length"},
      "has_urls":       {"value": "count", "minimum": "count",
                         "min_count": "count", "threshold": "count"},
      "has_entries":    {"value": "count", "minimum": "count",
                         "min_count": "count", "threshold": "count"},
      "urls_reachable": {"value": "count", "minimum": "count",
                         "min_count": "count", "threshold": "count"},
      "has_format":     {"value": "count", "minimum": "count",
                         "min_count": "count"},
  }
  ```
  `_require_assertion_keys` consults this table when flagging
  unknown keys: emit `" — did you mean {suggestion!r}?"` if the
  key is hinted, else empty suffix. Per-type keying is the
  single design nuance: after the rename, `pattern` is VALID
  for `regex` / `min_count`-type (so NO hint there), but
  `pattern` on `contains` should suggest `needle`.

- **DEC-010 — Keep the #61 handler-signature cross-check
  (Q10=A).** The test
  `test_handler_signature_agrees_with_constant` already iterates
  the constant and verifies each required key appears in the
  handler lambda source. After the rename, if both
  `ASSERTION_TYPE_REQUIRED_KEYS[type].required = {"needle"}`
  AND the handler reads `a.get("needle", "")`, the test passes
  without any test-file edit. We update the handler lambdas to
  read the new keys; the test follows automatically. If the
  introspection regex proves too rigid (e.g. fails on
  `a["needle"]` bracket access), we widen the regex in the same
  story — but expected outcome is no test-file change.

- **DEC-011 — Six-story breakdown with atomic docs landing
  (Q11=A).** See Detailed Breakdown below. Story order:
  (1) constant + handlers + drift-hints,
  (2) in-repo `.eval.json` migration + `init.py` scaffolding,
  (3) test-fixture migration + stale-hint-test updates,
  (4) docs + README + rubric,
  (5) Quality Gate,
  (6) Patterns & Memory.

- **DEC-012 — Extend `AssertionKeySpec` with per-key type info
  (natural consequence of DEC-002).** To enforce "native JSON
  ints only" at load time, `AssertionKeySpec` grows a
  `field_types: dict[str, type]` field. `_require_assertion_keys`
  adds a type-check pass: for each present key that has a
  declared type, verify `isinstance(val, expected)` and raise a
  helpful `ValueError` on mismatch (`"assertions[{i}]
  (type={type!r}): key 'length' must be int, got str 'abc'"`).
  Types: `needle`/`pattern`/`format` → `str`; `length`/`count`
  → `int`. String-typed ints on disk (`{"length": "500"}`)
  reject loudly.

### Session Notes

**Session 1 (2026-04-20)** — Discovery + scoping (Q1-Q8) +
Architecture (two concerns: Testing Strategy + Drift-Hint
Redesign) + Refinement (Q9-Q11 + DEC-012 as a natural
consequence of DEC-002). All in one session. User clarified
"no external users" which trimmed `schema_version` and
migration-tool scope (DEC-003=C, DEC-004=D). Architecture
review surfaced the per-type drift-hint design as the one
genuine design task; captured as DEC-009.

**Session 2 (2026-04-20) — US-006 Patterns & Memory closeout.**
Evaluated four candidates from the US-006 description:

- **Codified: `.claude/rules/per-type-drift-hints.md`** — the
  `dict[type, dict[wrong-key, right-key]]` shape generalizes to
  any future polymorphic-dict validator (grading_criteria scale
  types, section-field format types, trigger-test sub-types).
  The global-hint failure mode that motivated DEC-009's per-type
  keying is a genuine foot-gun — after a rename, some "wrong"
  keys become valid for a subset of types, and a global table
  silently mis-suggests in both directions.
- **Codified: `.claude/rules/constant-with-type-info.md`** — the
  `field_types: dict[str, type]` extension on `AssertionKeySpec`
  (DEC-012) generalizes to any mixed-primitive-type payload
  constant. Captured two concrete foot-guns specific enough to
  prevent drift: (a) `isinstance(True, int)` silently accepts
  bool-for-int without an explicit `bool is not int` guard, and
  (b) handler-side runtime coercion (`int(a.get(...))`) shifts
  error surfacing from load-time to opaque mid-run tracebacks.
- **Rejected update: `.claude/rules/eval-spec-stable-ids.md`** —
  verified the rule discusses only the `id` field, not payload
  keys. Zero mentions of `value`/`needle`/`pattern`/etc. No edit
  needed; the rule remains correctly scoped to id uniqueness.
- **Verified: per-type-key section in
  `docs/eval-spec-reference.md`** — US-004 already added
  "## Assertion types and per-type keys" (lines 119-157) with a
  complete per-type table and an example of each shape. The
  "Schema history" section (lines 207-219) documents the #67
  rename. Accurate and comprehensive; no edit needed.

The two new rule files reference each other as companion rules
(both live on the same validator seam in `schemas.py`) and each
names `plans/super/67-per-type-assertion-keys.md` as the
canonical implementation anchor. The rules are written
specifically enough to prevent future drift (named constants,
DEC-### pointers, test class names) and generically enough to
apply beyond #67 (framed around "polymorphic-dict loaders" and
"mixed-primitive-type payload constants").

---

## Detailed Breakdown (Stories)

Ordering: core rename (handler + constant + hints) → in-repo
spec migration → test-fixture migration → docs → Quality Gate
→ Patterns & Memory. US-002 / US-003 / US-004 can run in
parallel after US-001 lands.

Every story's acceptance criteria include the project
validation command from CLAUDE.md:
`uv run ruff check src/ tests/ && uv run pytest --cov=clauditor
--cov-report=term-missing` must pass with the 80% coverage
gate.

### US-001 — Rename assertion keys in constant, handlers, and drift-hints

**Description:** Flip the per-type key names across the three
code sites that share them: `ASSERTION_TYPE_REQUIRED_KEYS`
(schemas.py), `_ASSERTION_HANDLERS` (assertions.py), and the
new `_ASSERTION_DRIFT_HINTS` table. Extend `AssertionKeySpec`
with a `field_types` field per DEC-012 so native-int
validation lands atomically. The propose-eval prompt's per-type
table renders automatically from the updated constant — no
prompt code change needed (but one prompt-test string
assertion gets updated here, not in US-003, because it's
test-mechanical and belongs with the constant change).

**Traces to:** DEC-001, DEC-002, DEC-009, DEC-010, DEC-012.

**Acceptance Criteria:**

- `src/clauditor/schemas.py`:
  - `AssertionKeySpec` grows `field_types:
    dict[str, type] = field(default_factory=dict)` — frozen
    dataclass-compatible shape.
  - `ASSERTION_TYPE_REQUIRED_KEYS` rewritten per DEC-001 with
    DEC-012 types:
    - `contains`, `not_contains` → `AssertionKeySpec(required={"needle"}, field_types={"needle": str})`
    - `regex` → `AssertionKeySpec(required={"pattern"}, field_types={"pattern": str})`
    - `min_count` → `AssertionKeySpec(required={"pattern", "count"}, field_types={"pattern": str, "count": int})`
    - `min_length`, `max_length` → `AssertionKeySpec(required={"length"}, field_types={"length": int})`
    - `has_urls`, `has_entries`, `urls_reachable` → `AssertionKeySpec(optional={"count"}, field_types={"count": int})`
    - `has_format` → `AssertionKeySpec(required={"format"}, optional={"count"}, field_types={"format": str, "count": int})`
  - `_ASSERTION_DRIFT_HINTS` added per DEC-009 (concrete shape
    in DEC-009 above).
  - `_require_assertion_keys` rewritten:
    1. Unknown-key branch consults `_ASSERTION_DRIFT_HINTS[type].get(key)` for the hint; emit `" — did you mean {suggestion!r}?"` if present, else empty. Remove the current global `{"pattern","min","max"} → "value"` and `"threshold" → "minimum"` branches.
    2. New type-check pass: for each present key (required OR optional) in `spec.field_types`, verify `isinstance(val, expected)` — if mismatch, raise `ValueError(f"{ctx} (type={type!r}): key {key!r} must be {expected.__name__}, got {type(val).__name__} {val!r}")`. **`bool` is a subclass of `int` in Python**, so when `expected is int`, the check must also reject `bool` values (e.g., `{"length": True}`) — implemented as `isinstance(val, expected) and not (expected is int and isinstance(val, bool))`.
  - Error-message path order: unknown type → missing required → wrong type → unknown key. (Each is a distinct branch; no cascading noise.)
- `src/clauditor/assertions.py`:
  - `_ASSERTION_HANDLERS` updated to read new keys, with native int access (no `int(a.get(...))` coercion). **Required keys use direct `a[key]` access** so loader-bypass (test-only path) fails loudly with `KeyError` instead of silently returning a bogus default (e.g. `max_length` with default 0 would fail every output — CodeRabbit finding). Optional keys keep `.get(key, default)` for the legitimate "omitted → use default" case:
    - `contains`, `not_contains` → `a["needle"]`
    - `regex` → `a["pattern"]`
    - `min_count` → `a["pattern"]` + `a["count"]` (both required per DEC-001)
    - `min_length`, `max_length` → `a["length"]`
    - `has_urls`, `has_entries`, `urls_reachable` → `a.get("count", 1)` (optional, default 1)
    - `has_format` → `a["format"]` + `a.get("count", 1)` (format required, count optional)
  - The docstring at `assertions.py:463` (if it references `value` / schema shape) updated to mention the new keys.
- `tests/test_schemas.py`:
  - `TestAssertionKeySpec::test_contains_required_keys` parametrize table updated to the new per-type keys.
  - `TestAssertionKeySpec::test_handler_signature_agrees_with_constant` — verify it passes without edit (or widen the regex if introspection fails on the new key names).
  - New `TestAssertionKeySpec::test_field_types_match` — assert every `required ∪ optional` key has an entry in `field_types`, and every `field_types` key is in `required ∪ optional`.
  - `TestRequireAssertionKeys` — rewrite parametrize table:
    - Missing-required tests updated to new keys (e.g. `contains` missing `needle`).
    - Unknown-key tests updated: `pattern` on `contains` → hint `needle`; `value` on `regex` → hint `pattern`; `min` on `min_length` → hint `length`; `threshold` on `has_urls` → hint `count`; etc. Coverage: one hint test per (type, hinted wrong-key) pair in `_ASSERTION_DRIFT_HINTS`.
    - Two new wrong-type tests: `{"type":"min_length","length":"500"}` → `ValueError` mentioning "must be int, got str"; `{"type":"contains","needle":123}` → `ValueError` mentioning "must be str, got int".
  - Drop tests that asserted the OLD global hints (`"did you mean 'value'?"`, `"did you mean 'minimum'?"`) — replaced by the per-type hint tests above.
- `tests/test_propose_eval.py`:
  - `TestBuildProposeEvalPrompt::test_prompt_contains_per_type_table` — update pinned literal from `"min_count → required: value · optional: minimum"` to the new rendering (e.g. `"min_count → required: count, pattern"`).
  - `test_prompt_has_no_alias_keys` — update assertion: the prompt should NOT contain the strings `"'value'"`, `"'minimum'"` (anywhere suggesting the old keys are legit). `pattern` / `min` / `max` absence-assertions are relaxed — `pattern` IS now a valid key for two types and legitimately appears in the rendered table.
- `uv run ruff check src/ tests/` passes.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with ≥80% coverage.

**Done when:** `{"type":"contains", "id":"x", "needle":"foo"}`
loads cleanly via `EvalSpec.from_dict`; `{"type":"contains",
"id":"x", "value":"foo"}` raises `ValueError` at load with
`"assertions[0] (type='contains'): unknown key 'value' — did
you mean 'needle'?"`; `{"type":"min_length","id":"x","length":"500"}`
raises with `"key 'length' must be int, got str '500'"`.

**Files:**
- `src/clauditor/schemas.py` — `AssertionKeySpec` extension,
  constant rewrite, `_ASSERTION_DRIFT_HINTS`,
  `_require_assertion_keys` rewrite.
- `src/clauditor/assertions.py` — `_ASSERTION_HANDLERS` rewrite
  + docstring update.
- `tests/test_schemas.py` — `TestAssertionKeySpec`,
  `TestRequireAssertionKeys` updates.
- `tests/test_propose_eval.py` — two prompt-table assertion
  updates.

**Depends on:** none.

**TDD:**
1. Rewrite `TestAssertionKeySpec::test_contains_required_keys`
   parametrize table first (drives the constant change).
2. Rewrite `TestRequireAssertionKeys` parametrize table with
   the new (type, bad_entry, expected_substring) triples —
   covers missing / unknown / wrong-type for every type.
3. Add `test_field_types_match` before implementing the
   `field_types` field.
4. Run tests → fail → implement schema-side changes (constant,
   drift-hint table, validator logic) → tests pass.
5. Update handlers last (tests don't directly exercise the
   handler bodies until production-code tests run, but the
   `test_handler_signature_agrees_with_constant` will fail if
   the handler reads the wrong key).
6. Update the two prompt tests; verify they pass.

**Rules applied:**
- `pre-llm-contract-hard-validate.md` — the per-type validator
  (now also type-checking) is the canonical "prompt-side
  invariant + loader-side hard-fail" shape.
- `eval-spec-stable-ids.md` — same load-time hard-fail style
  as `_require_id`.
- `in-memory-dict-loader-path.md` — validator lives in
  `from_dict`; LLM path and on-disk path both inherit.
- `llm-cli-exit-code-taxonomy.md` — new validation errors
  route to exit 2 at the CLI via the existing
  `validation_errors` plumbing.

---

### US-002 — Migrate in-repo `.eval.json` files + `cli/init.py` scaffolding

**Description:** Hand-rewrite the two checked-in eval specs
and the `clauditor init` starter-assertion scaffolding to use
the new keys + native JSON ints. No tooling; the surface is
small (11 assertions + 4 scaffold entries).

**Traces to:** DEC-001, DEC-002, DEC-006.

**Acceptance Criteria:**

- `src/clauditor/skills/clauditor/assets/clauditor.eval.json`:
  - Every `value` key replaced with its per-type semantic key
    (`needle` for `contains`/`not_contains`; `length` for
    `min_length`). Length values switched from string
    (`"500"`) to native int (`500`).
  - No `value`, `minimum`, `threshold` keys remain anywhere
    in the file.
  - The file loads cleanly via `EvalSpec.from_file` (verify
    by running `uv run clauditor validate` on the bundled
    skill or equivalent).
- `examples/.claude/commands/example-skill.eval.json`:
  - Same migration: every assertion entry uses per-type
    semantic keys with native JSON ints.
  - All 8 assertions migrated: `contains` (2), `not_contains` (1),
    `regex` (2), `min_length` (1), `has_urls` (1), `has_entries` (1).
  - File loads cleanly.
- `src/clauditor/cli/init.py`:
  - Starter assertions at lines 55-58 rewritten to the new
    key shape.
  - The docstring or comment (if any) describing the starter
    assertions reflects the new keys.
- Add a regression test in `tests/test_bundled_skill.py`
  (extend existing class) that loads
  `clauditor.eval.json` via `EvalSpec.from_file` and asserts
  zero validation errors. This catches future migrations
  that miss the bundled spec.
- Add a regression test for the example spec: load
  `examples/.claude/commands/example-skill.eval.json` and
  assert zero validation errors. Prefer adding to
  `tests/test_spec.py` (existing integration coverage lives
  there).
- Add a regression test for `cli/init.py`: invoke
  `cmd_init` in a `tmp_path`, verify the generated
  `<skill>.eval.json` loads via `EvalSpec.from_file` without
  errors, AND verify the file does NOT contain the literal
  string `"value"` as a JSON key (simple substring check).
- Project validation passes.

**Done when:** `EvalSpec.from_file` returns without errors on
both checked-in specs AND on `cli/init.py`'s generated output.
No string-typed ints remain. `grep -rn '"value"'
src/clauditor/skills/ examples/.claude/commands/
src/clauditor/cli/init.py` returns zero matches (ignoring
grading_criteria and section field `format: "..."` which are
unrelated).

**Files:**
- `src/clauditor/skills/clauditor/assets/clauditor.eval.json`
- `examples/.claude/commands/example-skill.eval.json`
- `src/clauditor/cli/init.py`
- `tests/test_bundled_skill.py` (new regression test)
- `tests/test_spec.py` OR a new `tests/test_examples.py` (new
  regression test for example spec)
- Extend or add to existing `tests/test_cli_init.py` if it
  exists, otherwise add regression there in the appropriate
  CLI test module.

**Depends on:** US-001.

**TDD:**
1. Write the three regression tests first (load each spec,
   assert no errors).
2. Run → all three fail (old specs still have `value` keys
   which the new validator rejects).
3. Hand-edit the three files to the new shape.
4. Run → all three pass.

**Rules applied:**
- `json-schema-version.md` — NOT applicable (DEC-003=C).
- `path-validation.md` — any `input_files` on the migrated
  specs stay unchanged (validator already enforces).

---

### US-003 — Migrate all test fixtures + drop stale hint tests

**Description:** Hand-migrate every inline assertion dict in
the test suite (~100 occurrences across 12 files) to the new
keys. Drop or rewrite tests that pinned old drift-hint literals
or old `value` semantics.

**Traces to:** DEC-006, DEC-009.

**Acceptance Criteria:**

- Every `"value":` key inside an assertion-dict literal in
  `tests/**/*.py` replaced with the correct per-type key for
  its enclosing `type`. Integer values switched from string to
  native int where applicable.
- Test files in scope (heaviest first):
  - `tests/test_schemas.py` (~35 dicts)
  - `tests/test_assertions.py` (~20 dicts)
  - `tests/test_propose_eval.py` (~15 dicts — fixture shapes,
    NOT prompt strings)
  - `tests/test_cli.py` (~10 dicts)
  - `tests/conftest.py` (~3 dicts)
  - `tests/test_spec.py` (~5 dicts)
  - `tests/test_baseline.py` (~1 dict)
  - `tests/test_cli_transcript_slice.py` (~3 dicts)
  - `tests/test_cli_propose_eval.py` (~1 dict)
  - `tests/test_asserters.py` (~1 dict)
  - Any others surfaced by `grep -rn '"value"' tests/`.
- Stale tests dropped or rewritten:
  - Any test asserting `"did you mean 'value'?"` verbatim →
    drop or rewrite to assert the new per-type hints (already
    covered in US-001's rewrite of `TestRequireAssertionKeys`;
    sweep ensures no other file pins the old string).
  - Any test that relied on `value` being missing → defaults-
    to-1 behavior — update to use the new `count` key (with
    the optional-default-1 semantic preserved per DEC-005).
- Verify no test file contains the literal `"value":` inside
  an assertion dict by running a sweep grep (allowing
  `"value"` in rubric / trigger / other non-assertion
  contexts).
- Project validation passes with ≥80% coverage.
- Every test that existed before this story still passes
  after it, OR has been explicitly replaced by a new
  equivalent (document in commit message).

**Done when:** `uv run pytest` runs green; `grep -rn
'"value"' tests/ | grep -v grading_criteria | grep -v
'"value".*#.*rubric'` (or similar filter) returns only non-
assertion-dict hits.

**Files:** All test files under `tests/` that touch assertion
dicts — enumerated above. Plus any missed by the enumeration
but surfaced by the grep sweep.

**Depends on:** US-001.

**TDD:** Not pure TDD — this is a mechanical migration. Use
the existing test pass (green suite) as the regression guard.
Workflow:
1. Pick one test file at a time.
2. Update all `"value"` → per-type key, string ints → native
   ints.
3. Run the single file's tests; fix breakage.
4. Commit per file (or per small group) for reviewability.
5. Final grep sweep to confirm zero remaining `"value"` in
   assertion dicts.

**Rules applied:**
- `pytester-inprocess-coverage-hazard.md` — if any test uses
  `pytester.runpytest_inprocess` AND patches `clauditor.*`
  modules, remember the hazard. Preventive; unlikely to hit.
- `mock-side-effect-for-distinct-calls.md` — any test that
  mocks a function called multiple times with distinct values
  uses `side_effect=[...]`. Preventive.

---

### US-004 — Update docs, README, and the propose-eval prompt assertion-schema block

**Description:** Update every human-facing doc site that shows
an assertion JSON example to use the new keys. The propose-eval
prompt's per-type table renders automatically from the
(already-updated) constant, but the prompt also contains
hand-written example JSON and prose describing the schema —
those strings are updated here. Atomic with US-001/US-002/US-003
per DEC-007.

**Traces to:** DEC-007, DEC-001.

**Acceptance Criteria:**

- `README.md`:
  - The single assertion example at line ~125 updated to the
    new shape with native int.
  - No `"value":` strings remain in any assertion example.
  - Anchor text of any surrounding H2 (e.g. `## Eval Spec
    Format`, `## Quick Start`) stays byte-identical per
    `readme-promotion-recipe.md`.
- `docs/quick-start.md`:
  - Four inline assertion examples at lines 20-23 updated.
- `docs/eval-spec-reference.md`:
  - The complete-spec example at lines 61-65 updated.
  - If the doc has a per-type field table, it's regenerated
    or hand-updated to the new keys. If no such table
    exists, ADD one (small section) — the redesign is an
    excellent reason to have explicit per-type docs.
  - Add a short "Schema history" note documenting the
    rename (one paragraph, in a `## Changelog`-style
    section at the bottom if one doesn't exist, else append).
- `src/clauditor/propose_eval.py`:
  - Any hand-written example assertion JSON inside the
    prompt (outside the auto-rendered table) updated to the
    new keys.
  - Any prose description of what keys an assertion carries
    updated.
  - Prompt-builder tests from US-001 still pass.
- `src/clauditor/assertions.py`:
  - Docstring at line ~463 (or wherever the schema shape is
    documented) updated.
- `src/clauditor/skills/clauditor/SKILL.md`:
  - No current `value` references in assertion examples —
    verify via grep. No change expected; this is a sanity
    check per `bundled-skill-docs-sync.md` ("workflow
    unchanged → no cascade").
- Doc-example regression test: add a test
  `tests/test_docs_examples.py` (new file) or extend an
  existing one that greps the in-tree markdown for
  assertion dict literals and verifies any it finds parse
  cleanly via `EvalSpec.from_dict` (minimal spec wrapping).
  Alternative: if this is too finicky, settle for a simpler
  grep-based test that asserts no markdown file under
  `README.md` / `docs/` contains the string `"value":`
  inside a code fence labeled `json` that also contains
  `"type":`.
- Project validation passes.

**Done when:** Every markdown file is `"value":`-free in
assertion contexts, all prompt tests still pass, and the
bundled SKILL.md is unchanged (zero doc-sync cascade per
`bundled-skill-docs-sync.md`).

**Files:**
- `README.md`
- `docs/quick-start.md`
- `docs/eval-spec-reference.md`
- `src/clauditor/propose_eval.py` (hand-written assertion
  examples / prose, NOT the auto-rendered table)
- `src/clauditor/assertions.py` (docstring)
- `tests/test_docs_examples.py` (new) or extension to
  existing doc-lint tests.

**Depends on:** US-001 (the validator must already accept the
new keys before the docs can show them).

**TDD:**
1. Write the doc-grep regression test first (asserting no
   `"value":` in assertion JSON examples).
2. Run → fails (current docs all have `"value"`).
3. Hand-edit each doc file to the new shape.
4. Run → passes.

**Rules applied:**
- `readme-promotion-recipe.md` — anchor-preservation:
  heading text stays byte-identical when editing
  content underneath. No H2 renames.
- `bundled-skill-docs-sync.md` — verified NOT applicable
  (SKILL.md workflow unchanged; only reference-material
  docs change).

---

### US-005 — Quality Gate

**Description:** Run code reviewer 4× across the full
changeset (US-001 through US-004). Address every real bug.
Run CodeRabbit after PR is non-draft. Re-run project
validation.

**Traces to:** project-wide quality standards.

**Acceptance Criteria:**
- Code-reviewer agent run 4 times across the full diff. Each
  pass's findings triaged and fixed (or documented as false
  positive in a session note) before the next pass.
- CodeRabbit review triaged if PR is out of draft.
- `uv run ruff check src/ tests/` passes.
- `uv run pytest --cov=clauditor --cov-report=term-missing`
  passes with ≥80% coverage.
- No failing tests, no new lint findings, no open reviewer
  objections.

**Done when:** All reviewer passes clean (or false-positive-
documented), CodeRabbit satisfied if applicable, project
validation green.

**Files:** Whatever reviewer passes surface.

**Depends on:** US-001, US-002, US-003, US-004.

---

### US-006 — Patterns & Memory

**Description:** Evaluate patterns worth codifying after the
redesign lands. Candidates (to be evaluated, not pre-
committed):
- Per-type drift-hints as a general pattern for "loader
  rejects unknown keys with type-specific guidance". If a
  future feature's loader wants the same shape (e.g. a
  grading_criteria validator, a section-field validator),
  codify as a `.claude/rules/` rule.
- The `field_types` extension on `AssertionKeySpec` as a
  "single-source-of-truth constant with type info" pattern.
  Candidate rule file if useful elsewhere.
- Update `.claude/rules/eval-spec-stable-ids.md` if it
  mentions old keys (verify and fix).
- Update or add a per-type-key section to
  `docs/eval-spec-reference.md` if US-004 did not already.

**Traces to:** standard closeout pattern.

**Acceptance Criteria:**
- Every candidate pattern has been (a) codified in
  `.claude/rules/<name>.md`, (b) documented in `docs/`, or
  (c) explicitly evaluated and rejected with a one-line
  note in this plan's Session Notes.
- No regression in existing rules — new rules are additive.
- If docs update lands, README teaser (if any) adjusted per
  `readme-promotion-recipe.md`.

**Done when:** New rule/docs files committed OR Session
Notes contain "evaluated X, Y, Z — chose to defer because …".

**Files:** `.claude/rules/*.md`, `docs/*.md` (TBD),
`plans/super/67-per-type-assertion-keys.md` (Session Notes).

**Depends on:** US-005.

---

## Beads Manifest

- **Worktree:** `/home/wesd/dev/worktrees/clauditor/67-per-type-assertion-keys`
- **Branch:** `feature/67-per-type-assertion-keys`
- **PR:** https://github.com/wjduenow/clauditor/pull/69

### Task graph

| Bead ID | Story | Priority | Depends on |
|---|---|---|---|
| `clauditor-xo7` | Epic — #67: Redesign assertion schema with per-type semantic keys | P2 | — |
| `clauditor-xo7.1` | US-001 — Rename assertion keys in constant, handlers, and drift-hints | P2 | none (ready) |
| `clauditor-xo7.2` | US-002 — Migrate in-repo eval specs and cli/init.py scaffolding | P2 | US-001 |
| `clauditor-xo7.3` | US-003 — Migrate test fixtures and drop stale hint tests | P2 | US-001 |
| `clauditor-xo7.4` | US-004 — Update docs, README, and propose-eval prompt prose | P2 | US-001 |
| `clauditor-xo7.5` | US-005 — Quality Gate (code-reviewer ×4 + CodeRabbit + validation) | P2 | US-001, US-002, US-003, US-004 |
| `clauditor-xo7.6` | US-006 — Patterns & Memory (rules and docs from redesign) | P3 | US-005 |

### Ready to work

- `clauditor-xo7.1` (US-001) — **entry point; US-002, US-003, US-004 unblock in parallel once this lands.**
