# Super Plan: #63 — Surface runner API errors and interactive-hang signals instead of "failed to run: None"

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/63
- **Branch:** `feature/63-runner-error-surfacing`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/63-runner-error-surfacing`
- **Phase:** `detailing`
- **PR:** _(pending publish)_
- **Sessions:** 1
- **Last session:** 2026-04-20

---

## Discovery

### Ticket summary

**What:** Two related runner bugs that both surface as the
uninformative CLI string `ERROR: Skill failed to run: None`:

1. **API errors (429, auth) surface as `None`.** When Anthropic
   returns a 429 rate-limit (or 401/403 auth error), the `claude -p
   --output-format stream-json` subprocess emits a `result` message
   with `is_error: true` and the human-readable error text in the
   `result` string field. The runner's parser reads `usage` from
   that message but ignores both `is_error` and `result`. Exit code
   is often `0`, stderr is empty, so `SkillResult.error` stays
   `None` and the CLI prints `"failed to run: None"` even though
   the real error is one assistant-message away.
2. **Interactive skills hang or degrade silently.** A skill that
   invokes `AskUserQuestion` or emits plain assistant text ending
   with a question produces no usable output — stdout text is a
   dangling question, `stop_reason == "end_turn"`, `num_turns == 1`,
   `exit_code == 0`, `stderr` is empty. The user sees either
   "failed to run: None" or (worse) a "success" verdict on an
   incomplete transcript.

**Why:** Both failure modes collapse to the same unhelpful string,
so the user cannot distinguish "429 → wait and retry" from "auth →
fix env var" from "skill needs input → rewrite test_args" from
"skill is broken." Every incident burns debug time on a problem the
subprocess already told us about.

**Done when:**
- `clauditor validate` against a skill that triggers a 429 (or auth
  error) prints the actual error string from the `result` stream
  message, not `None`.
- `clauditor validate` against an interactive skill that ends its
  single turn asking a question prints a specific warning naming
  the detected shape, rather than "succeeded" or "failed to run:
  None."
- All CLI rendering paths that consult `SkillResult.error` fall
  back to an informative default instead of printing `None`.
- `docs/stream-json-schema.md` documents the `is_error` field and
  the error-bearing `result` string on `result` messages, per the
  `.claude/rules/stream-json-schema.md` concurrent-update contract.
- Coverage stays ≥80%; ruff passes.

### Key findings — codebase scout

#### Bug site 1: `src/clauditor/runner.py::_invoke` stream-json parser (lines 274–291)

```python
elif mtype == "result":
    saw_result = True
    usage = msg.get("usage") or {}
    if isinstance(usage, dict):
        try:
            input_tokens = int(usage.get("input_tokens", 0) or 0)
        except (TypeError, ValueError):
            input_tokens = 0
        try:
            output_tokens = int(usage.get("output_tokens", 0) or 0)
        except (TypeError, ValueError):
            output_tokens = 0
```

- Reads only `usage`. Never reads `is_error`, `result` (error text),
  or `subtype` / `stop_reason`.
- `is_error` *is* already illustrated in the success-case fixture
  in `tests/conftest.py::make_fake_skill_stream` (emits
  `is_error: false`), so the infrastructure is one line away from
  carrying the true case.

#### Bug site 2: `src/clauditor/runner.py` line 337 (normal-exit SkillResult construction)

```python
error=stderr_text if returncode != 0 and stderr_text else None,
```

`error` is populated **only** from subprocess stderr when
`returncode != 0`. For a 429, `returncode` can be `0` (Claude CLI
emits a structured result + exits clean), and `stderr_text` is
often empty because the error came through stream-json. Result:
`error = None`.

#### CLI render paths that print `None`

| File | Line | Current template |
|---|---|---|
| `src/clauditor/cli/validate.py` | 128–130 | `f"ERROR: Skill failed to run: {skill_result.error}"` |
| `src/clauditor/cli/extract.py` | 97–98 | `f"ERROR: Skill failed: {skill_result.error}"` |
| `src/clauditor/cli/capture.py` | 70–72 | `f"ERROR: Skill run failed (exit {result.exit_code}): {result.error}"` |
| `src/clauditor/cli/grade.py` | 368–371, 394–397 | `f"ERROR: Skill failed: {primary_skill_result.error}"` / `f"ERROR: Variance skill run failed: {variance_result.error}"` |
| `src/clauditor/cli/run.py` | 29–30 | `f"ERROR: {result.error}"` (silent when `error is None`) |
| `src/clauditor/spec.py` | 190 | `result.error or "Unknown error"` — already null-safe, canonical example for the rest |

All five CLI sites converge on the same pattern; only `spec.py`
applies the `or "Unknown error"` fallback. A uniform fix + a
richer `SkillResult.error` together close the bug.

#### `SkillResult` shape

```python
@dataclass
class SkillResult:
    output: str
    exit_code: int
    skill_name: str
    args: str
    duration_seconds: float = 0.0
    error: str | None = None
    outputs: dict[str, str] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    raw_messages: list[dict] = field(default_factory=list)
    stream_events: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and self.output.strip() != ""
```

Note `warnings: list[str]` already exists for observability —
load-bearing for the interactive-hang detection story.

#### Downstream `SkillResult` consumers

- `spec.py` lines 140–193 — reads `succeeded` and `error` (only
  consumer that null-coalesces).
- `baseline.py` lines 47, 96 — carries `SkillResult` through
  `BaselineReports` for metadata; reads output / tokens /
  duration; does **not** read `error`.
- `benchmark.py` lines 40–41, 96 — assumes success; does not
  read `error`.
- `grader.py`, `quality_grader.py` — take raw output strings,
  not `SkillResult`.

Only CLI rendering + `spec.py` care about `error`. Adding
structured error fields is cheap (no non-CLI consumer has
assumptions to break).

#### `docs/stream-json-schema.md` current status

- Documents `type: "result"`, `subtype: "success"`, `usage`, and
  the defensive token-count pattern.
- Shows `is_error: false` in an example but does **not** document
  the field's semantics, the `is_error: true` case, or the `result`
  string that carries error text.
- No failure-case example (429, auth, interactive hang).

Per `.claude/rules/stream-json-schema.md`, this doc must be
updated in the same commit that teaches the parser new fields.

#### Test landscape

- `tests/test_runner.py` is class-organized
  (`TestRunRaw`, `TestStreamJsonRunner`,
  `TestStreamJsonDefensiveBranches`, `TestSkillResultWarnings`,
  etc.) — natural home for `TestStreamJsonErrorResult` and
  `TestInteractiveHangDetection`.
- Canonical fixture `make_fake_skill_stream` in
  `tests/conftest.py` accepts an `extra_messages` list, which is
  the plumbed injection point for `is_error: true` result
  messages.
- No existing test exercises `is_error: true`. No existing test
  exercises an assistant message ending with `?` + no second turn.

#### `--eval` path is not special

`clauditor validate skill.md --eval skill.eval.json` loads a
different eval spec file but runs through the same
`SkillSpec.from_file` → `SkillRunner.run` → `_invoke` path as the
auto-discovery form. Both paths surface the bug identically.

### Applicable `.claude/rules/`

| Rule | Applies? | Constraint on this plan |
|---|---|---|
| `stream-json-schema.md` | **yes** | New field reads must use `.get(...)` with falsy-safe defaults; skip-and-warn on malformed (don't raise); update `docs/stream-json-schema.md` in the same commit; new field names land in `raw_messages` / `stream_events` regardless. |
| `pure-compute-vs-io-split.md` | **yes** | Parser stays pure (no stderr emission from inside `_invoke`); richer error classification goes into `SkillResult` fields; the CLI layer owns the user-facing message. Pure classifier fn (`classify_stream_result(msg) -> (error_msg, category) \| None`) is the natural extraction. |
| `mock-side-effect-for-distinct-calls.md` | **yes** | New tests that mock subprocess/runner across more than one call per test use `side_effect=[...]`, not shared `return_value`. |
| `pytester-inprocess-coverage-hazard.md` | **yes** | New runner tests must not combine `pytester.runpytest_inprocess` + `mock.patch` on already-instrumented modules under `--cov`. Not expected to come up; flag if it does. |
| `monotonic-time-indirection.md` | conditional | Existing `SkillRunner` duration path already uses `time.monotonic`. If we add new duration tracking inside a new classifier, follow the alias pattern. Unlikely to apply here. |
| `json-schema-version.md` | no | No new persisted sidecars — stream-json is third-party, read-only. |
| `pre-llm-contract-hard-validate.md` | no | Stream-json is defensively parsed, not hard-validated; `is_error` is observational, not an invariant over our output. |
| `llm-cli-exit-code-taxonomy.md` | no | The runner is a subprocess wrapper, not an LLM-wrapping command. `validate` / `grade` are already past the LLM call layer for this scope. |
| `centralized-sdk-call.md` | no | No direct Anthropic SDK usage; the subprocess is what we parse. |
| `data-vs-asserter-split.md` | no (conditional) | If we add new fields to `SkillResult`, they stay pure-data — do not add behavior methods to the dataclass. |
| `bundled-skill-docs-sync.md` | no | No edit to the bundled skill's workflow. |
| `readme-promotion-recipe.md` | no | No README restructure expected; a single-line link-from-README to the updated schema doc is well under the trigger threshold. |
| CLAUDE.md | yes | Use `bd` for task tracking, class-based tests, `asyncio_mode="strict"`, 80% coverage gate, ruff + pytest validation command. |
| `workflow-project.md` | n/a | No such file at repo root; no project-workflow customizations. |

### Proposed scope

A) **Core fix — API error surfacing** (must):
   1. Extend `_invoke`'s `"result"` branch to read `is_error` and
      the `result` string (defensively, with `.get` + type guards).
      On `is_error: true`, capture the error text (falling back to
      `api_error_status` / a generic "API error") into a local.
   2. Thread the captured text into `SkillResult.error` at the
      construction sites, giving it precedence over stderr-derived
      text (or merging them so both are visible).
   3. Optionally add a structured `error_category: Literal[
      "rate_limit", "auth", "api", "interactive", "subprocess",
      None]` discriminator on `SkillResult` for downstream
      consumers that want to branch on category without regex'ing
      the string.

B) **Interactive-hang detection** (must, but scope-graded):
   1. **Short-term (always):** document that `validate` cannot
      drive interactive skills; all params must be inline.
   2. **Medium-term (likely):** in the parser, when the stream
      ends with `stop_reason == "end_turn"`, `num_turns == 1`, and
      final assistant text ends with `?` after strip, append a
      `warnings[]` entry and (if `output` would otherwise read as
      success) force a specific CLI message — "Skill appears to
      have asked for input; ensure all parameters are in
      test_args."
   3. **Deferred / out of scope:** `stdin_script` on `EvalSpec`
      (ticket explicitly flags this as "longer term").

C) **Uniform CLI rendering** (must):
   - Null-safe `result.error or "<descriptive fallback>"` at every
     CLI print site, matching `spec.py:190`'s canonical shape.
   - When `SkillResult.error_category` is set (if we add it) or
     `SkillResult.warnings` contains an interactive-hang flag,
     print a category-aware message.

D) **Docs + rule** (must, per `stream-json-schema.md`):
   - Update `docs/stream-json-schema.md` to document `is_error`,
     the `result` error string, and one failure-case example
     (429). Mention interactive-hang shape as a known ambiguity.
   - Update `.claude/rules/stream-json-schema.md`'s canonical
     snippet if the parser shape changes meaningfully.

E) **Tests** (must):
   - `TestStreamJsonErrorResult` — 429, auth, generic API
     `is_error: true` cases; malformed `is_error` (string, None)
     falls through to the old path; no `result` field degrades
     gracefully.
   - `TestInteractiveHangDetection` — assistant text ends with
     `?`, single turn, `end_turn` → warning appended and CLI
     message renders accordingly.
   - `TestSkillResultSucceeded` — update to reflect whatever
     `succeeded` change (if any) comes out of refinement.
   - CLI render tests: uniform fallback, category-aware message.

### Open questions for the user

See _Scoping Questions_ below.

---

## Scoping Questions

**Answered 2026-04-20:**
- **Q1 = A** — Stream-json `result` text wins; stderr appended
  into `warnings[]`.
- **Q2 = A** — Add structured `error_category: Literal[...]` to
  `SkillResult` (enum-like, pure-data).
- **Q3 = A** — Tier 1 + Tier 2: document limitation + heuristic
  detection (single-turn + `end_turn` + trailing `?`) with a
  specific warning. No `stdin_script` in this PR.
- **Q4 = D** — All five CLI commands, via a shared
  `_render_skill_error(result) -> str` helper so the template
  lives in one place.
- **Q5 = D** — Add a new `succeeded_cleanly` property (strict);
  leave `succeeded` as-is for back-compat. Consumers that care
  about interactive-hang opt in to the stricter check.
- **Q6 = D (provisional)** — Hybrid fixture strategy: extend
  `make_fake_skill_stream` with an `error_text` kwarg AND add a
  `make_fake_interactive_hang_stream` sibling for `?`-detection
  cases. Correct in refinement if preferred otherwise.

### Q1 — Error precedence when both stream-json `result` and stderr carry text

When `is_error: true` arrives in a `result` message AND `stderr`
is non-empty (e.g. Claude CLI also logs to stderr), which wins?

- **A.** Stream-json `result` text wins; stderr is appended into
  `warnings[]` for observability. *Rationale: the structured
  error is more likely to be user-actionable; stderr is often
  debug noise.*
- **B.** Concatenate both into `error`, stream-json first, stderr
  second. *Richest output, but double-prints when stderr echoes
  the same text.*
- **C.** Stderr wins if non-empty; stream-json fills in only when
  stderr is empty. *Preserves the current "stderr is the error"
  mental model.*
- **D.** Prefer stream-json when `is_error: true`, else stderr.
  *Mixed: stream-json authoritative for API errors, stderr for
  subprocess-level failures.*

### Q2 — Add a structured `error_category` field to `SkillResult`?

A discriminator (`"rate_limit" | "auth" | "api" | "interactive"
| "subprocess" | None`) lets CLI layers branch without regex'ing
the error string.

- **A.** Yes — add it. Pure-data field, enum-like, populated by
  the parser. Enables category-aware CLI messages ("retry later"
  vs "fix ANTHROPIC_API_KEY" vs "all params must be inline").
- **B.** No — keep `error: str | None` only. Regex on the text
  where needed; minimize surface area of the dataclass.
- **C.** Yes, but as a `str` (free-form short tag), not a Literal.
  Cheaper to extend later without a schema change.
- **D.** Defer to a follow-up ticket. Do the minimum for this
  plan (string-only).

### Q3 — Interactive-hang response

The ticket lists three tiers (doc-only / detect-and-warn /
stdin_script). What's in scope for this PR?

- **A.** Tier 1 + Tier 2 — document the limitation AND detect the
  heuristic shape (single turn, end_turn, assistant text ends with
  `?`) and emit a specific warning. No `stdin_script` support.
- **B.** Tier 1 only — add a CLI-level note / doc update; no
  detection logic. Smallest, safest; but the user still sees
  "failed to run: None" for a hang.
- **C.** Tier 1 + 2 + 3 — full `stdin_script` support on
  `EvalSpec`. Large scope; pulls in spec schema changes.
- **D.** Tier 2 only, no docs. Heuristic detection is the
  user-facing value; doc update can trail.

### Q4 — Scope of CLI-render fix (null-safe fallback + category-aware messages)

Which CLI commands get the updated rendering in this PR?

- **A.** All five: `validate`, `run`, `capture`, `grade`,
  `extract`. Uniform shape across the product. Largest blast
  radius but highest value.
- **B.** `validate` + `grade` only (the two commands users run
  most often on flaky skills). Other three get a follow-up.
- **C.** `validate` only (matches the ticket's repro exactly).
  Minimal scope.
- **D.** All five, but behind a shared helper `_render_skill_error(
  result: SkillResult) -> str` so the template lives in one
  place. *Best for maintainability if we go wide.*

### Q5 — `SkillResult.succeeded` semantics

Today: `exit_code == 0 and output.strip() != ""`. Should an
interactive-hang (detected via heuristic) explicitly flip this
to false?

- **A.** Yes — if the parser flags an interactive-hang shape,
  `succeeded` returns false, even if output is non-empty (a
  dangling question counts as "didn't actually run"). Surfaces
  the failure mode uniformly.
- **B.** No — leave `succeeded` alone; the warning is the
  user-facing signal. Preserves back-compat for any test that
  asserts on `succeeded`.
- **C.** Yes, but only when `is_error: true` was also emitted —
  don't couple heuristic detection to the success flag.
- **D.** Add a new property `succeeded_cleanly` (strict) and
  leave `succeeded` as-is. Consumers pick which they want.

### Q6 — Test-fixture surface

How do we drive the new tests?

- **A.** Extend `make_fake_skill_stream` in `tests/conftest.py`
  with an optional `error_text: str | None = None` kwarg that, if
  set, replaces the `result` message payload with an
  `is_error: true` + `result: <text>` shape. Shared fixture for
  all error tests.
- **B.** New sibling fixture `make_fake_error_stream` —
  dedicated to error cases, keeps `make_fake_skill_stream`
  minimal.
- **C.** Inline NDJSON in each test — explicit but repetitive.
- **D.** A+B hybrid — extend the existing fixture with
  `error_text`, AND add a `make_fake_interactive_hang_stream`
  for the `?`-detection cases (different enough from
  API-error cases that sharing is awkward).

---

## Architecture Review

**Reviewed 2026-04-20.** Four parallel subagents: Data+API, Observability, Testing, Security+Performance.

| Area | Rating | Summary |
|---|---|---|
| SkillResult backward-compat | pass | New `error_category: Literal[...] \| None = None` is strictly additive; three constructor call sites already omit it; no `asdict(result)` / serialization path. |
| Literal enum choice | pass | Five categories enumerate cleanly at plan time; `"api"` acts as catch-all; adding a 6th member later is an additive union change. |
| `succeeded_cleanly` property | pass | Strict predicate: `succeeded and error is None and error_category is None and no interactive-hang warning`. `spec.py`, `baseline.py`, `benchmark.py` stay on lenient `succeeded`; new callers opt in. |
| CLI helper `_render_skill_error` | pass | Returns error *tail* (not the full `ERROR: ...` line). Lives in `src/clauditor/cli/__init__.py`. Callers keep their prefix context ("Skill failed to run:", "Variance skill run failed:"). |
| Stream-json field reads | pass | Strict `is_error is True` check (per defensive-parse rule); `msg.get("result")` for error text; `subtype` not needed. |
| Pure-compute extraction | pass | Two pure helpers: `_classify_result_message(msg) -> (error_text, category)` and `_detect_interactive_hang(stream_events, final_text) -> bool`. Live as private `_*` in `runner.py`. |
| Version compatibility | pass | Missing `is_error` → `None` → skip classification; older `claude` CLI degrades to current behavior. Test case pins this. |
| JSON sidecar implications | pass | `error_category` is runtime-only; not serialized to `baseline.json` / `grading.json` / `assertions.json`. No `schema_version` bump needed. Document the invariant in-code. |
| Transcripts redaction | pass | API error strings are low-risk (429/auth text is user-facing, not secret); existing regex + key-based scrubs in `transcripts.py::redact` cover any embedded key-shaped strings. |
| Error-category → retry hint | pass | `CATEGORY_HINTS: dict[str,str]` lookup inside `_render_skill_error`. Pure compute. |
| Prompt-injection dataflow | pass | `SkillResult.error` never flows into `suggest`'s prompt (the `SuggestInput` dataclass doesn't carry it); not in scope regardless. |
| `succeeded` semantics (back-compat) | pass | Q5=D leaves `succeeded` untouched; purely additive. |
| Parser performance | pass | Two `.get()` calls per `result` message (~1/run); heuristic is O(text-len) once at EOF. Negligible. |
| Testing strategy | pass | Fixture hybrid (extend + sibling) is cleaner than `extra_messages`; no pytester+patch hazard; 80% gate achievable via 5 new test classes. |
| Stderr hygiene (truncation) | **concern** | Long stream-json `result` strings (200+ chars) print to stderr unbounded across 5 CLI sites. No existing truncation helper in `cli/`. Needs a policy — truncate at N chars with ellipsis, or document "API errors are printed in full." |
| `warnings[]` field usage | **BLOCKER** | `SkillResult.warnings` is currently write-only: five CLI render sites do not consult it, nor do `spec.py` / `baseline.py` / `benchmark.py`. The plan's Q1=A strategy (stderr → `warnings[]` when stream-json wins) is observationally inert without a reader. Must establish at least one reader or revise Q1. |
| `error_category` observability surface | concern | Unspecified: (a) does it print inline in CLI messages (`ERROR: [rate_limit] ...`) or stay metadata-only? (b) does it persist to any sidecar for historical grep-ability? Per Q2=A it's a public dataclass field, but the CLI-rendering/persistence contract needs a decision. |
| Interactive-hang heuristic precision | concern | Text-ends-with-`?` is both false-positive-prone (rhetorical questions in a successful response) and false-negative-prone (`"Please provide foo"` doesn't end in `?`). Recommend also matching `AskUserQuestion` tool-use events in `stream_events`; consider an escape-hatch opt-out on the EvalSpec. |
| `succeeded` caller audit | concern | With `succeeded_cleanly` added but `succeeded` unchanged, callers that meant "actually completed" may be wrong on an interactive-hang. Need a caller-by-caller audit in refinement to decide which migrate. |
| Pytest-plugin public API docs | concern | `SkillResult.error_category` will land in the fixture surface; `docs/pytest-plugin.md` currently doesn't enumerate fields. One-line doc entry needed (or prefix with `_` if internal-only — but Q2=A implies public). |
| Stream-json `result` string DoS | concern | Unbounded `result` string lands verbatim in `stream_events` → `run-*/output.jsonl`. Realistic risk is low (Claude CLI is first-party), but a soft cap (e.g., 4KB + ellipsis) with a warning is cheap insurance. |
| Timeout-path precedence regression | concern | Current `runner.py:306` early-returns on timeout before the normal-exit path. If a future refactor removes that early return, Q1=A precedence (stream-json wins) could mask a legitimate `"timeout"` error with a pre-timeout `is_error: true` message. Either pin the invariant with a comment or add `error_category: "timeout"` so "timeout" cannot be clobbered. |

**Blockers to resolve:** 1 (`warnings[]` usage).
**Concerns to resolve:** 7 (stderr truncation, category surface, hang heuristic precision, `succeeded` caller audit, pytest-plugin docs, `result`-string DoS, timeout precedence).

Moving to Phase 3 to resolve these — see _Refinement Log_ below.

---

## Refinement Log

**Resolved 2026-04-20** — user accepted recommended defaults
(B1-a, C1-A/N=1000, C2-C, C3-D, C4-C, C5-A, C6-A, C7-C). The
decisions below capture every constraint that flows into story
design.

### Decisions

**DEC-001 — Stream-json precedence.**
When a `result` message arrives with `is_error: true`, its
`result` string wins as `SkillResult.error`. Any subprocess-
`stderr` text collected during the run is moved into
`SkillResult.warnings` for observability. Basis: Q1=A. Answers
"who owns the error string" with one rule; stderr stays
forensically available without double-printing.

**DEC-002 — Warnings reader: `_render_skill_error` surfaces
warnings inline.**
The new CLI render helper (DEC-011) appends `(stderr: <first
non-empty line of warnings>)` as a trailing line after the main
error message whenever `result.warnings` is non-empty. This makes
`SkillResult.warnings` observationally live (resolves architecture
blocker **B1**). First line only — full warnings remain in the
JSON sidecars/stream_events.

**DEC-003 — Error-text truncation at 1000 chars.**
`_render_skill_error` truncates any error string over 1000 chars
with the suffix `" ... (truncated; see stream_events)"`.
Protects terminals from 10+ KB dumps without hiding the full text
(it stays in `stream_events` → `run-*/output.jsonl`). Basis:
C1-A with N=1000.

**DEC-004 — `error_category` renders as a hint line, not an
inline tag.**
The CLI message keeps a human-prose first line; when
`error_category` is set, a second line follows with the retry
hint from `CATEGORY_HINTS` (e.g. `"Hint: retry in ~60s (rate
limit)"`). No inline `[rate_limit]` bracket. Basis: C2-C. Keeps
prose readable; category is still programmatically accessible
via the dataclass field.

**DEC-005 — Interactive-hang detection uses OR of two signals +
EvalSpec escape hatch.**
`_detect_interactive_hang` returns True when either (a) the
final assistant text after `.strip()` ends with `?`, OR (b) any
event in `stream_events` has `type=tool_use` with
`name=AskUserQuestion`. EvalSpec gains
`allow_hang_heuristic: bool = True` (default on); when `False`,
detection skipped. Basis: C3-D. Catches both plain-text-prompt
and structured-tool-use cases; the opt-out handles skills whose
prose legitimately ends with a rhetorical question.

**DEC-006 — `spec.py:186` migrates to `succeeded_cleanly`; other
callers stay on lenient `succeeded`.**
`spec.py`'s assertion-failure branch should reflect "actually
completed cleanly", so it moves to the strict predicate. Every
other `succeeded` caller (`baseline.py`, `benchmark.py`, the five
CLI files) keeps the lenient predicate — they only need to know
"did we get output" for their downstream logic. Basis: C4-C.

**DEC-007 — `docs/pytest-plugin.md` enumerates public
`SkillResult` fields.**
The doc gains a one-line field list mentioning `output`,
`exit_code`, `error`, `error_category`, `succeeded`,
`succeeded_cleanly`, `input_tokens`, `output_tokens`, and
`duration_seconds`. Everything else (`raw_messages`,
`stream_events`, `warnings`) is marked internal-observability.
Basis: C5-A.

**DEC-008 — 4 KB soft cap on stream-json `result` strings.**
The parser truncates the `result` string at 4 KB with
`" ... (truncated)"` before storing in `SkillResult.error`.
The full message still lands in `stream_events`. Defense against
ill-behaved upstream payloads and keeps `SkillResult.error`
usable across CLI render / sidecar paths. Basis: C6-A.

**DEC-009 — Timeout gets `error_category: "timeout"` + pinning
comment.**
The timeout-path `SkillResult` construction
(`runner.py:306–319`) sets `error_category = "timeout"`. A
comment above the early-return documents: "Must stay an early
return — a post-timeout stream-json `is_error: true` must not
clobber the timeout message." Basis: C7-C.

**DEC-010 — `SkillResult.error_category` is a `Literal[...] |
None` with six categories.**
Enum members: `"rate_limit"`, `"auth"`, `"api"`,
`"interactive"`, `"subprocess"`, `"timeout"`. Default `None`.
Additive to the dataclass; no constructor-callsite changes
required. Basis: Q2=A (expanded from 5 to 6 per DEC-009).

**DEC-011 — Shared CLI helper `_render_skill_error`.**
Lives in `src/clauditor/cli/__init__.py`. Signature:
`_render_skill_error(result: SkillResult, *, unknown_fallback:
str = "Unknown error") -> str`. Returns the error *tail* only
(callers keep their prefix like `"Skill failed to run: "`).
Contains the `CATEGORY_HINTS: dict[str, str]` lookup table
(DEC-004) and the truncation logic (DEC-003). Basis: Q4=D.

**DEC-012 — Five CLI commands adopt the helper.**
`validate.py`, `run.py`, `capture.py`, `grade.py`, `extract.py`
— every `f"...: {result.error}"` render site routes through
`_render_skill_error(result)`. Basis: Q4=D.

**DEC-013 — Pure-helper extraction:
`_classify_result_message` + `_detect_interactive_hang`.**
Two private `_*` pure helpers in `runner.py`. The first takes a
single stream-json `result` message dict and returns
`(error_text, category)`. The second takes `stream_events` +
`final_text` and returns a `bool`. Both are unit-testable
without subprocess mocking; `_invoke` calls them at the
appropriate seam per `.claude/rules/pure-compute-vs-io-
split.md`. Basis: architecture review area 6.

**DEC-014 — Fixture hybrid: extend
`make_fake_skill_stream` + add
`make_fake_interactive_hang_stream`.**
`make_fake_skill_stream` gains `error_text: str | None = None`
kwarg; when set, the final `result` message carries
`is_error: True` and `result: <error_text>`. New sibling
fixture `make_fake_interactive_hang_stream(text: str =
"What would you like?", use_tool_use: bool = False)` emits the
hang-shape stream (single turn, `end_turn`, trailing `?`, or
`AskUserQuestion` tool_use when `use_tool_use=True`). Defaults
preserve `is_error: False` for back-compat. Basis: Q6=D.

**DEC-015 — `error_category` is runtime-only; no sidecar
serialization.**
No `schema_version` bump needed for any existing sidecar
(`baseline.json`, `assertions.json`, `grading.json`,
`history.jsonl`). Document the invariant with a one-line comment
on the `error_category` field: "runtime-only — do not serialize
to sidecars without bumping their schema_version." Basis:
architecture review area 8.

**DEC-016 — `docs/stream-json-schema.md` and
`.claude/rules/stream-json-schema.md` updated in the same PR.**
Per the rule: any new field the parser reads must be documented
in the schema doc. Additions: `is_error: bool` on result
messages, `result: str` error text, one failure-case example
(429). The canonical snippet in the rule file updates to
include the new `is_error` / `result` branch so future
maintainers follow the pattern. Basis: stream-json-schema rule.

### Session Notes — Refinement

- No decisions reversed from Phase 1 answers.
- One architecture blocker (**B1**, `warnings[]` is write-only)
  resolved by DEC-002 — the render helper becomes the reader.
- Seven concerns each mapped to a decision; no additional
  ambiguities surfaced during the resolve.

---

## Detailed Breakdown

Stories ordered by dependency. Validation after every code-
touching story: `uv run ruff check src/ tests/` and
`uv run pytest --cov=clauditor --cov-report=term-missing` (80%
gate).

---

### US-001 — Extend `SkillResult` dataclass + test fixture hybrid

**Description:** Foundational model change. Add the new
`error_category` field and `succeeded_cleanly` property to
`SkillResult`, and land the hybrid fixture shape so subsequent
stories have a natural way to construct error/hang streams in
tests.

**Traces to:** DEC-010, DEC-011 (shape of field consumed by
helper), DEC-014 (fixture hybrid), DEC-015 (runtime-only
invariant).

**Files:**
- `src/clauditor/runner.py` — add `error_category:
  Literal["rate_limit","auth","api","interactive","subprocess",
  "timeout"] | None = None` with the "runtime-only" comment;
  add `succeeded_cleanly` property.
- `src/clauditor/__init__.py` — confirm `SkillResult` remains
  exported (no change expected).
- `tests/conftest.py` — extend `make_fake_skill_stream` with
  `error_text: str | None = None`; add
  `make_fake_interactive_hang_stream(text: str = "What would
  you like?", use_tool_use: bool = False)` sibling.
- `tests/test_runner.py` — new class
  `TestSkillResultErrorCategory`.

**TDD:**
- `succeeded_cleanly` returns True only when: `succeeded` is
  True AND `error is None` AND `error_category is None` AND no
  `stream_events` matches an interactive-hang warning tag.
  (For US-001 scope, the interactive-hang check reduces to a
  `warnings`-list contains-substring probe; US-003 wires in the
  real detector.)
- Each `error_category` Literal value can be assigned and
  round-trips through the constructor with no type-coercion.
- Default `error_category` on every existing constructor call
  site stays `None` (regression test: construct with every
  current call-site's kwargs and assert
  `.error_category is None`).
- Fixture: `make_fake_skill_stream("hello",
  error_text="boom")` yields NDJSON whose final result message
  has `is_error: True, result: "boom"`; default path still
  emits `is_error: False`.
- Fixture: `make_fake_interactive_hang_stream()` yields NDJSON
  with `num_turns == 1`, `stop_reason == "end_turn"`, assistant
  text ending with `?`, `is_error: False`.
- Fixture: `make_fake_interactive_hang_stream(use_tool_use=
  True)` injects a `{"type": "tool_use", "name":
  "AskUserQuestion", ...}` block in the assistant message's
  content list.

**Depends on:** none.

**Done when:** tests pass; coverage ≥80%; ruff clean; no
existing test regresses.

---

### US-002 — Parser classifies `is_error: true` result messages

**Description:** Teach the stream-json parser to read `is_error`
and `result` on result messages, classify the category by
keyword, apply the 4 KB soft cap, and populate
`SkillResult.error` / `.error_category` with stream-json
precedence (stderr moves to `warnings[]`).

**Traces to:** DEC-001 (precedence), DEC-008 (4 KB cap),
DEC-010 (category values), DEC-013 (pure helper extraction).

**Files:**
- `src/clauditor/runner.py` — new pure helper
  `_classify_result_message(msg: dict) -> tuple[str | None,
  str | None]`; extend the `elif mtype == "result":` branch to
  call it when `msg.get("is_error") is True`; thread results
  into the normal-exit construction at line 337. Keep the old
  stderr-driven path as fallback when stream-json carries no
  error.
- `tests/test_runner.py` — new class
  `TestStreamJsonIsErrorResult` and extensions to
  `TestStreamJsonDefensiveBranches`.

**TDD:**
- 429 case: `result` text contains "429" or "rate limit"
  (case-insensitive) → `error_category == "rate_limit"`, error
  text matches payload.
- Auth case: `result` text contains "auth" / "401" / "403" /
  "unauthorized" / "ANTHROPIC_API_KEY" → category `"auth"`.
- Generic API case: any other `is_error: true` → category
  `"api"`, verbatim text.
- Back-compat: `is_error` field absent → behaves identically
  to today; no category assigned; `error` sourced from stderr
  when `returncode != 0`.
- Defensive: `is_error` is the string `"true"` (not bool) →
  treat as absent (the `is True` check is strict).
- Missing `result` field with `is_error: true` → error text
  falls back to `"API error (no detail)"`; category still set.
- 4 KB cap: `result` string of 10 KB → stored `error` is 4
  KB + `" ... (truncated)"`; `stream_events` keeps the full
  message.
- Precedence: stream-json `is_error: true` + non-empty stderr
  + `returncode == 0` → `error` is stream-json text; first
  non-empty stderr line appears in `warnings`.
- Precedence: no stream-json error + `returncode != 0` +
  stderr → pre-existing behavior unchanged (`error ==
  stderr_text`).

**Depends on:** US-001.

**Done when:** all TDD cases pass; coverage ≥80%; ruff clean.

---

### US-003 — Interactive-hang detection + EvalSpec escape hatch

**Description:** Add the pure hang-detector helper, wire it in
at EOF, and let EvalSpec opt out via `allow_hang_heuristic`.
When detected (and not opted out), append a specific warning
and set `error_category = "interactive"`.

**Traces to:** DEC-005 (OR of two signals + escape hatch),
DEC-010 (category), DEC-013 (pure helper), DEC-014 (fixture).

**Files:**
- `src/clauditor/runner.py` — new pure helper
  `_detect_interactive_hang(stream_events: list[dict],
  final_text: str) -> bool`; call site inside `_invoke` at EOF;
  a canonical warning string (e.g. `"skill may have asked for
  input — ensure all parameters are in test_args (heuristic)"`).
- `src/clauditor/schemas.py` — add
  `allow_hang_heuristic: bool = True` on `EvalSpec`; round-trip
  through `from_dict` / `from_file` with defaulting; no
  `schema_version` bump (field is optional and additive —
  confirm in test that legacy eval.json without the key still
  loads clean).
- `src/clauditor/spec.py` — thread the flag into the runner
  invocation so `_invoke` knows whether to invoke the detector.
- `tests/test_runner.py` — new class
  `TestInteractiveHangDetection`.
- `tests/test_schemas.py` — extend `TestEvalSpecFromFile` with
  the new field's default + explicit-value cases.

**TDD:**
- Trailing `?`: final assistant text `"What's your name?"`,
  single turn, `end_turn` → True.
- Trailing `?` with leading/trailing whitespace → True (`.strip()`
  handles it).
- `AskUserQuestion` tool_use present in stream_events → True
  even when final text has no `?`.
- Neither signal → False.
- Multi-turn run (`num_turns > 1`) → False regardless of
  trailing `?` (heuristic narrows to "didn't actually make
  progress").
- `allow_hang_heuristic: False` in EvalSpec → detector never
  fires even when both signals match; no warning appended.
- EvalSpec default round-trips as `True`; existing eval.json
  fixtures without the key still load.
- Legit rhetorical-question case gated by the escape hatch:
  a success-path skill whose final text ends `"Questions?"` with
  `allow_hang_heuristic: False` → `succeeded = True`, no warning.

**Depends on:** US-001.

**Done when:** all TDD cases pass; every existing EvalSpec
fixture loads unchanged; coverage ≥80%; ruff clean.

---

### US-004 — Timeout path sets `error_category = "timeout"`

**Description:** Small hardening: label timeout runs with the
new category discriminator and pin the early-return invariant
that prevents stream-json error-masking a timeout.

**Traces to:** DEC-009 (timeout category + invariant comment),
DEC-010 (category list includes "timeout").

**Files:**
- `src/clauditor/runner.py` — at the timeout construction
  (current line ~307–318): add `error_category="timeout"`;
  above the early-return at ~line 306, add a short
  load-bearing comment: `# Early return is load-bearing: a
  post-timeout stream-json is_error:true must not clobber the
  "timeout" error.`
- `tests/test_runner.py` — extend `TestStreamJsonRunner`'s
  timeout tests (or add `TestTimeoutErrorCategory`).

**TDD:**
- Timeout path sets `error_category == "timeout"`.
- Timeout path still produces `error == "timeout"` (message
  unchanged for back-compat).
- Regression: a stream-json `is_error: true` delivered *before*
  the timeout killed the process does NOT leak into the timeout
  `SkillResult.error` (assert on the early-return invariant).

**Depends on:** US-001.

**Done when:** tests pass; coverage ≥80%; ruff clean.

---

### US-005 — Shared CLI helper `_render_skill_error`

**Description:** Centralize the user-facing error message in
one pure function. Handles the `None`-fallback, category-driven
hint line, stderr-warning trailer, and 1000-char truncation.

**Traces to:** DEC-002 (warnings surfaced inline), DEC-003
(1000-char cap), DEC-004 (hint-line render), DEC-011 (signature
+ location).

**Files:**
- `src/clauditor/cli/__init__.py` — new helper
  `_render_skill_error(result: SkillResult, *,
  unknown_fallback: str = "Unknown error") -> str`; module-
  level `CATEGORY_HINTS: dict[str, str]` (e.g.
  `"rate_limit": "Hint: retry in ~60s (rate limit)"`,
  `"auth": "Hint: check the ANTHROPIC_API_KEY environment
  variable"`, `"interactive": "Hint: ensure all parameters
  are in test_args; /clauditor cannot drive interactive
  skills"`, `"timeout": "Hint: skill exceeded the run
  timeout"`, `"subprocess": "Hint: the claude CLI itself
  errored — see stream_events"`, `"api": "Hint: see the
  error text above"`).
- `tests/test_cli.py` — new class `TestRenderSkillError`.

**TDD:**
- `error is None`, no category → returns `unknown_fallback`.
- `error is None`, `error_category == "rate_limit"` → returns
  only the hint line (`CATEGORY_HINTS["rate_limit"]`).
- `error == "API error X"`, `error_category == "rate_limit"`
  → returns two-line string: main error, then hint.
- Long error (2000 chars) → truncated at 1000 with
  `" ... (truncated; see stream_events)"`.
- `warnings = ["line1\nline2", "line3"]` → a final
  `(stderr: line1)` trailer line appears (first non-empty
  line, first warning only — subsequent warnings stay in the
  list but don't expand the render).
- `warnings = []` → no trailer.
- Empty string `error = ""` treated same as `None` (consistent
  with spec.py's `or "Unknown error"` idiom).

**Depends on:** US-001, US-002, US-003, US-004 (so the helper
has the full category surface to render).

**Done when:** helper tests pass; coverage ≥80%; ruff clean.

---

### US-006 — Five CLI commands adopt the helper + regression E2E

**Description:** Every CLI site that currently prints
`{result.error}` into a failure message routes through the
shared helper. Add two end-to-end regression tests that pin the
exact user-facing strings for the ticket's two repros (429 +
interactive hang).

**Traces to:** DEC-011 (helper returns tail only; callers keep
prefix), DEC-012 (all five commands).

**Files:**
- `src/clauditor/cli/validate.py` (line ~128–130)
- `src/clauditor/cli/run.py` (line ~29–30)
- `src/clauditor/cli/capture.py` (line ~70–72)
- `src/clauditor/cli/grade.py` (lines ~368–371 AND ~394–397 —
  two call sites)
- `src/clauditor/cli/extract.py` (line ~97–98)
- `tests/test_cli.py` — per-command integration tests + new
  regression class `TestCmdValidateErrorSurfacingRegression`
  covering the ticket's two repros.

**TDD:**
- `validate.py` — on mocked 429 stream (`make_fake_skill_stream(
  "", error_text="API Error: Request rejected (429) ...")`),
  stderr contains the full 429 text + the `rate_limit` hint
  line. NO occurrence of the substring `": None"` or
  `"Unknown error"`.
- `validate.py` — on mocked interactive-hang stream, stderr
  contains the interactive hint line AND the warning string;
  no "failed to run: None".
- `run.py`, `capture.py`, `grade.py` (both sites),
  `extract.py` — each: same two cases, per-command prefix
  preserved ("Skill failed to run:", "Skill run failed (exit
  X):", "Skill failed:", "Variance skill run failed:").
- Mock discipline: any test that mocks `grade_quality` or
  `SkillRunner.run` more than once per test uses
  `side_effect=[...]` per `mock-side-effect-for-distinct-
  calls.md`.
- No test uses `pytester.runpytest_inprocess` +
  `mock.patch` per
  `pytester-inprocess-coverage-hazard.md`.

**Depends on:** US-005.

**Done when:** all per-command tests pass; the two ticket
repros produce the documented user-facing strings; coverage
≥80%; ruff clean.

---

### US-007 — Migrate `spec.py` assertion-failure branch to `succeeded_cleanly`

**Description:** The assertion-failure path in `spec.py` means
"the skill didn't actually complete cleanly" — exactly what
`succeeded_cleanly` was introduced to express. Switch that one
call site; every other `succeeded` caller stays lenient.

**Traces to:** DEC-006 (caller migration), DEC-010
(`succeeded_cleanly` predicate).

**Files:**
- `src/clauditor/spec.py` — flip `if not result.succeeded:`
  at line ~186 to `if not result.succeeded_cleanly:`. Leave
  the earlier `if self.eval_spec and result.succeeded:` at
  line ~144 on the lenient predicate — that gate is "did we
  get *any* output at all, even imperfect" which remains the
  right lenient check.
- `tests/test_spec.py` — new cases covering: interactive-hang
  run produces an assertion-failure with the right message;
  clean 429 run also produces an assertion-failure; a
  lenient-success run (non-empty output, no error/category,
  no hang) still passes through to assertion evaluation.

**TDD:**
- Interactive-hang run: `result.succeeded` is True (lenient —
  output is a question string), but `succeeded_cleanly` is
  False → `spec.evaluate` emits the failure message.
- 429 run with empty output: both `succeeded` and
  `succeeded_cleanly` are False (existing behavior) → no
  change in message path.
- Normal success: both True → assertion evaluation runs as
  before.

**Depends on:** US-001, US-003 (detector must actually set
the warning that `succeeded_cleanly` inspects).

**Done when:** tests pass; coverage ≥80%; ruff clean.

---

### US-008 — Update `docs/stream-json-schema.md` + rule-file snippet

**Description:** Document the two new fields the parser now
reads, add a 429 failure example, and update the canonical
snippet in `.claude/rules/stream-json-schema.md` so the
defensive-parsing pattern includes the `is_error`/`result`
branch.

**Traces to:** DEC-016 (doc update contract).

**Files:**
- `docs/stream-json-schema.md` — add:
  1. `is_error: bool` entry on `result` messages —
     documented as "tolerated-if-missing; `True` indicates the
     Claude CLI encountered an API or subprocess error and the
     `result` string carries human-readable detail".
  2. `result: str` entry on result messages — documented as
     "the error text when `is_error: true`; absent on success".
  3. One failure-case NDJSON example (429).
  4. A "how clauditor responds" bullet cross-referencing the
     new `SkillResult.error_category` surface without naming the
     internal helper.
- `.claude/rules/stream-json-schema.md` — update the canonical
  snippet's `elif mtype == "result":` branch to show the new
  `.get("is_error")` / `.get("result")` reads with defensive
  guards. Keep the skip-and-warn invariant intact.

**TDD:** n/a (docs-only). Verification:
- Ruff / pytest unaffected.
- Grep check: `is_error` now appears in both files.
- Manual read: new example parses as valid NDJSON.

**Depends on:** US-002 (doc describes what the parser does;
parser must land first).

**Done when:** the two files reflect DEC-016; review read-
through finds no missing / stale references to "only `usage`
is read from `result`".

---

### US-009 — Update `docs/pytest-plugin.md` with public `SkillResult` fields

**Description:** Single-paragraph field-list addition so tests
that assert on `error_category` / `succeeded_cleanly` have a
documentation anchor.

**Traces to:** DEC-007.

**Files:**
- `docs/pytest-plugin.md` — one-paragraph field list enumerating
  the public fields (`output`, `exit_code`, `error`,
  `error_category`, `succeeded`, `succeeded_cleanly`,
  `input_tokens`, `output_tokens`, `duration_seconds`), with a
  one-sentence note that `raw_messages`, `stream_events`, and
  `warnings` are internal-observability-only and may change.

**TDD:** n/a (docs-only).

**Depends on:** US-001 (field must exist).

**Done when:** doc updated; grep shows `error_category`
referenced in `docs/pytest-plugin.md`.

---

### US-010 — Quality Gate

**Description:** Four passes of `code-reviewer` across the full
changeset, fixing every real bug each pass. CodeRabbit review
if available. Final validation: `uv run ruff check src/ tests/`
and `uv run pytest --cov=clauditor --cov-report=term-missing`
with the 80% gate green.

**Traces to:** all decisions.

**Files:** whatever the reviews flag; expect touch-ups in
`runner.py`, `cli/__init__.py`, and the new test classes.

**Acceptance:**
- 4 × code-reviewer passes; every actionable finding addressed
  or explicitly marked false-positive with reasoning.
- CodeRabbit review clean (or findings addressed).
- ruff + pytest + 80% coverage green.
- No stale `TODO:` / `FIXME:` in new code.

**Depends on:** US-001, US-002, US-003, US-004, US-005, US-006,
US-007, US-008, US-009 (all implementation complete).

**Done when:** all three validation commands green; all review
findings addressed.

---

### US-011 — Patterns & Memory

**Description:** Capture any new patterns learned during
implementation. Candidate rule edits (to confirm or reject at
the end):

- `.claude/rules/stream-json-schema.md` — already updated in
  US-008 with the new canonical snippet; if implementation
  reveals a non-trivial new invariant (e.g. "error categories
  are keyword-classified, not taxonomy-lookup"), capture it
  as a short addendum.
- `.claude/rules/pure-compute-vs-io-split.md` — consider
  adding a new canonical anchor entry for
  `_classify_result_message` + `_detect_interactive_hang` as
  another "pure-helper-extraction" example (the "Fifth anchor"
  convention continues).
- `.claude/rules/` (new?) — if rendering precedence proves
  to be a repeatable pattern (stream source vs stderr source
  across future integrations), consider codifying
  `.claude/rules/structured-error-precedence.md`. Only land if
  a genuine second anchor exists.
- `docs/stream-json-schema.md` is already updated in US-008.

**Traces to:** all decisions (captures what stuck).

**Acceptance:**
- At least the `pure-compute-vs-io-split.md` canonical-
  implementations section updated with the new anchor.
- Any other rule changes scoped to "would this benefit a
  future plan author?" — if no, skip without regret.
- MEMORY.md untouched unless a feedback / user memory lands
  in the session.

**Depends on:** US-010.

**Done when:** new rule anchor landed (or rejection documented
in the session notes); plan document Meta block updated to
reflect shipped state.

---

### Rules-compliance cross-check (all stories)

| Rule | Stories affected | How it's honored |
|---|---|---|
| `stream-json-schema.md` | US-002, US-003, US-008 | `.get(...)` defensive reads; skip-and-warn on malformed; doc updated in same PR. |
| `pure-compute-vs-io-split.md` | US-002, US-003, US-005 | Three new pure helpers (`_classify_result_message`, `_detect_interactive_hang`, `_render_skill_error`); I/O at call sites. |
| `mock-side-effect-for-distinct-calls.md` | US-006 | Multi-call mocks use `side_effect=[...]`. |
| `pytester-inprocess-coverage-hazard.md` | US-006 | No new tests combine `runpytest_inprocess` + `mock.patch` under `--cov`. |
| `data-vs-asserter-split.md` | US-001 | `error_category` is pure data; `succeeded_cleanly` is a `@property` (analogous to existing `succeeded`). No `assert_*` methods added. |
| `json-schema-version.md` | n/a (reviewed) | No sidecar shape changes. |
| `bundled-skill-docs-sync.md` | n/a | No edits to the bundled `/clauditor` SKILL.md workflow. |
| `readme-promotion-recipe.md` | n/a | No README restructure required. |
| CLAUDE.md test conventions | all TDD blocks | Class-based organization; `tmp_path` for file tests; no direct `tempfile`. |
| CLAUDE.md task-tracking | all | `bd` on devolve; no TodoWrite/TaskCreate. |

---

## Beads Manifest

_(Populated on devolve.)_

---

## Session Notes

**Session 1 (2026-04-20):**
- Fetched ticket #63, created worktree + branch
  `feature/63-runner-error-surfacing`.
- Ran parallel Codebase Scout + Convention Checker subagents.
- Key bug sites pinned: `runner.py:274–291` (parser ignores
  `is_error`), `runner.py:337` (error only from stderr), five CLI
  render paths.
- Load-bearing rules: `stream-json-schema.md`,
  `pure-compute-vs-io-split.md`,
  `mock-side-effect-for-distinct-calls.md`,
  `pytester-inprocess-coverage-hazard.md`.
- Presented 6 scoping questions; awaiting user answers before
  Phase 2 (Architecture Review).
