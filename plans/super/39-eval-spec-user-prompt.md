---
ticket: "#39 (clauditor-iag)"
title: Add EvalSpec.user_prompt field
phase: devolved
branch: feature/39-user-prompt
worktree: feature/39-user-prompt
sessions: 1
---

# #39 — Add `EvalSpec.user_prompt` field

## Meta

- **Ticket:** https://github.com/wjduenow/clauditor/issues/39
- **Beads:** `clauditor-iag` (P3)
- **Related PRs:** #24 (blind A/B judge), #38 (`blind_compare_from_spec`)
- **Relevant rules:**
  - `.claude/rules/pure-compute-vs-io-split.md` (second anchor explicitly
    calls this feature out — both callers pick up the new field
    automatically through `blind_compare_from_spec`)
  - `.claude/rules/eval-spec-stable-ids.md` (does NOT apply — `user_prompt`
    is a plain scalar, not a list of addressable entries)
  - `.claude/rules/path-validation.md` (does NOT apply — string content,
    not a filesystem path)

## 1. Discovery

### What and why

`clauditor compare --blind` currently repurposes `EvalSpec.test_args` as
the natural-language user query handed to the LLM judge in
`build_blind_prompt`. `test_args` is semantically the skill runner's CLI
argument string (e.g. `--depth quick --limit 10`), not a user query. When
the test invocation happens to be plain English it works by accident;
when it is structured flags the prompt-injection `<user_prompt>` block
wraps meaningless CLI args as the supposed user intent.

`blind_compare_from_spec` (added in #38) is the single resolution point
for both callers:

- `src/clauditor/cli.py::_run_blind_compare` — CLI wrapper for
  `clauditor compare --blind`
- `src/clauditor/pytest_plugin.py::clauditor_blind_compare` — pytest
  fixture

Adding `EvalSpec.user_prompt` and teaching the shared helper to prefer it
over `test_args` is exactly the use case the
`pure-compute-vs-io-split.md` rule anticipates: one resolution helper,
two callers, zero drift.

### Codebase surface

Files to touch:

| File | Change |
| --- | --- |
| `src/clauditor/schemas.py` | Add `user_prompt: str \| None = None` field on `EvalSpec`; load it in `from_file`; emit in `to_dict` when set |
| `src/clauditor/quality_grader.py` | Update `validate_blind_compare_spec` + `blind_compare_from_spec` to read `spec.eval_spec.user_prompt` (no fallback — DEC-001) |
| `tests/test_schemas.py` | Round-trip load/save tests for the new field |
| `tests/test_quality_grader.py` | Resolution tests for `blind_compare_from_spec`: `user_prompt` required; missing `user_prompt` raises even when `test_args` is set |

Files NOT touched (important negative scope):

- `src/clauditor/spec.py::SkillSpec.run` — still uses `test_args` as
  skill-runner CLI args. The two semantics stay distinct.
- `src/clauditor/cli.py` — no CLI surface change; the Running-progress
  print keeps using `test_args` since that is what the runner receives.
- `_run_baseline_phase` — still passes `test_args` to `runner.run_raw`,
  correct because it is a runner-arg context.
- Any `.eval.json` schema-version bump — `EvalSpec` is input-only and has
  no `schema_version` field today; adding an optional key is backwards
  compatible.

### Stable-id decision

`user_prompt` is a single optional scalar, not a list of
per-entry-addressable entries. The `.claude/rules/eval-spec-stable-ids.md`
rule applies to **list-of-entries** fields (assertions, section fields,
grading_criteria). A single nullable string is out of scope. **No id
field.**

## 2. Architecture Review

Quick review — this is a one-field addition with no new I/O, no new
subprocess call, no LLM prompt restructuring beyond a different source
string for the same existing `<user_prompt>` block.

| Area | Rating | Notes |
| --- | --- | --- |
| Security | **pass** | String flows through the existing prompt-injection-hardened `build_blind_prompt` XML fence. No new untrusted surface. |
| Performance | **pass** | No queries, no loops, no extra API calls. |
| Data model | **concern** | Optional field — existing on-disk `.eval.json` files must keep loading. `from_file` must default to `None` when absent, and `to_dict` must only emit the key when set (so round-tripping a legacy spec does not add noise). |
| API design | **pass** | Dataclass-internal. No public CLI flag. |
| Observability | **concern** | Should we emit a stderr warning when `test_args` is used as the fallback AND looks like CLI flags (starts with `-`)? Ticket proposes "ideally" — refine in Phase 3. |
| Testing | **pass** | Unit tests cover both `from_file` round-trip and `blind_compare_from_spec` resolution. No integration test needed — the resolution logic is a pure helper. |

No blockers.

## 3. Refinement Log

### DEC-001: No fallback — `user_prompt` is required for blind compare

Library is pre-publication; backcompat is not a constraint. The cleanest
shape is a hard split: `test_args` keeps its runner-CLI-args semantics
*only*; `user_prompt` is the *only* source the blind judge consults. Any
existing eval spec that was relying on `test_args`-as-user-query is
updated in this same change.

**Why:** a fallback shim is the worst-of-both-worlds — it keeps the
ambiguity the ticket set out to remove, and bloats every new reader of
`blind_compare_from_spec` with a two-source resolution. Users confirmed
pre-publication status, so there is no migration burden to justify the
shim.

**Consequences:**
- `blind_compare_from_spec` raises `ValueError` when
  `spec.eval_spec.user_prompt` is missing/empty. The error message names
  `user_prompt` specifically.
- Any test fixture or eval.json in the repo that was using `test_args`
  as the judge query migrates to `user_prompt` in this change.
- No stderr warning heuristic. No deprecation docstring. No follow-up
  bead.

### DEC-002: `user_prompt` is a plain scalar, no stable id

Single optional string, not a list-of-entries, so
`.claude/rules/eval-spec-stable-ids.md` does not apply. No `id` field,
no load-time uniqueness check.

### DEC-003: `to_dict` emits `user_prompt` only when set

Round-tripping a spec that does not set `user_prompt` should not inject
a `"user_prompt": null` key. Match the existing pattern for
`output_file` / `trigger_tests` / `variance` / `grade_thresholds`.

## 4. Detailed Breakdown

Single coherent change — one implementation story plus Quality Gate and
Patterns & Memory. Too small to split further.

### US-001 — Add `user_prompt` field and require it in blind compare

**Description:** Add `EvalSpec.user_prompt: str | None = None`, wire it
through `from_file` and `to_dict`, and flip `blind_compare_from_spec` +
`validate_blind_compare_spec` to read `user_prompt` instead of
`test_args`. Migrate any repo-internal eval.json / test fixtures that
used `test_args` as the judge query.

**Traces to:** DEC-001, DEC-002, DEC-003.

**Files:**

- `src/clauditor/schemas.py`
  - Add `user_prompt: str | None = None` to `EvalSpec` (dataclass
    field, after `test_args`).
  - In `from_file`: `user_prompt=data.get("user_prompt")`. No validation
    beyond "if present, must be a non-empty string" — empty string is
    rejected so callers do not have to disambiguate `None` vs `""`.
  - In `to_dict`: emit `"user_prompt": self.user_prompt` only when the
    attribute is not `None`, after the `test_args` key.

- `src/clauditor/quality_grader.py`
  - `validate_blind_compare_spec`: replace the `test_args or ""` read
    with `spec.eval_spec.user_prompt or ""`. Update error message to
    `"blind_compare_from_spec: eval_spec.user_prompt must be set (used
    as the user prompt context for the judge)"`.
  - `blind_compare_from_spec`: same swap — `user_prompt = spec.eval_spec.user_prompt or ""`.
  - Update the module docstring for `blind_compare_from_spec` — the
    current copy still says "eval_spec.test_args must be set", which
    becomes false after this change.

- `tests/test_schemas.py`
  - New test: `from_file` parses `user_prompt` from a JSON fixture.
  - New test: `from_file` raises `ValueError` when `user_prompt` is
    present but an empty string or non-string.
  - New test: `to_dict` omits `user_prompt` when unset; includes it
    when set. Round-trip via `json.dumps`/`EvalSpec.from_file`
    equivalence.

- `tests/test_quality_grader.py`
  - `validate_blind_compare_spec`: test that missing `user_prompt`
    raises, regardless of `test_args` content. (Explicitly assert that
    a spec with `test_args="something"` and no `user_prompt` still
    raises — this is the core behavior change.)
  - `blind_compare_from_spec`: test that the judge prompt the downstream
    `blind_compare` sees contains the `user_prompt` value, not the
    `test_args` value. Patch `blind_compare` with `AsyncMock` to
    capture the forwarded `user_prompt` arg.

- **Repo-wide migration:** grep for `test_args` in `tests/*.py`,
  `tests/conftest.py`, `examples/.claude/commands/example-skill.eval.json`,
  and the `plans/super/*.md` cross-references. For each site whose
  `test_args` value is a natural-language query (not CLI flags), add
  `user_prompt=...` and either delete the `test_args=...` or leave it
  as empty string if the runner still needs a CLI invocation. The
  `_run_blind_compare` / `blind_compare_from_spec` path is the only one
  whose semantics change; everything else that *actually* passes
  `test_args` to the runner keeps working unchanged.

**TDD:**

1. Write `test_blind_compare_from_spec_requires_user_prompt` — asserts
   `ValueError` mentioning `user_prompt` when `user_prompt` is unset.
   Expected red.
2. Write `test_blind_compare_from_spec_forwards_user_prompt` — patches
   `clauditor.quality_grader.blind_compare` with `AsyncMock`, calls the
   helper, asserts the captured `user_prompt` kwarg equals the spec's
   `user_prompt` and NOT its `test_args`. Expected red.
3. Write `test_evalspec_from_file_loads_user_prompt` and its omit/empty
   variants. Expected red.
4. Implement the field + resolution swap. Tests go green.
5. Migrate in-repo fixtures; run full `uv run pytest --cov=clauditor`
   and confirm coverage gate holds.

**Done when:**
- `uv run ruff check src/ tests/` clean
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with
  the 80% gate
- `blind_compare_from_spec` reads `user_prompt`, never `test_args`
- No remaining repo grep hit where `test_args` is used as the judge
  query

**Depends on:** none.

**Rules consulted:**
- `pure-compute-vs-io-split.md` — this ticket is the canonical example
  the rule cites. Shape already in place; no refactor needed.
- `eval-spec-stable-ids.md` — checked and determined not applicable
  (scalar, not list-of-entries). See DEC-002.
- `mock-side-effect-for-distinct-calls.md` — not applicable here; the
  blind_compare mock is a single call per test, `return_value` is
  fine.

### Quality Gate

**Description:** Run the code-reviewer agent 4x over the full
changeset, fixing every real bug each pass. Run CodeRabbit review if
CI provides it. Final `uv run pytest --cov=clauditor` + `uv run ruff
check` both clean.

**Depends on:** US-001.

### Patterns & Memory

**Description:** Update `.claude/rules/` or docs if the implementation
surfaced a new pattern. Expected outcome: **no new rule** — this
ticket is a textbook application of the existing
`pure-compute-vs-io-split.md` anchor, so the existing rule's second
canonical anchor should be verified still accurate (the helper name
and file path) and that's it. If verification finds drift, update the
rule; otherwise close the story with a note that the pattern already
covers this case.

**Depends on:** Quality Gate.

## 5. Beads Manifest

- **Epic:** `clauditor-1qd` — #39: Add EvalSpec.user_prompt field
- **US-001:** `clauditor-49m` — Add user_prompt field + require it in blind_compare_from_spec
- **Quality Gate:** `clauditor-am6` — code review x4 + CodeRabbit (blocked by clauditor-49m)
- **Patterns & Memory:** `clauditor-x8y` — verify pure-compute-vs-io-split anchor (blocked by clauditor-am6)

Worktree: `/home/wesd/dev/worktrees/clauditor/feature/39-user-prompt`
Branch: `feature/39-user-prompt`
Plan PR: https://github.com/wjduenow/clauditor/pull/40
