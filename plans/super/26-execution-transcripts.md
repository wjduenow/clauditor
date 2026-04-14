# Super Plan: #26 — Capture execution transcripts for root-cause analysis

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/26
- **Branch:** `feature/26-execution-transcripts`
- **Worktree:** `/home/wesd/Projects/worktrees/clauditor/26-execution-transcripts`
- **Phase:** `detailing`
- **PR:** _pending_
- **Sessions:** 1
- **Last session:** 2026-04-14

---

## Discovery

### Ticket Summary

**What (as written):** Wire `clauditor grade my-skill --transcript` to produce a
jsonl transcript alongside the grade report, with failing assertions referencing
it. Persist stream-json to `.clauditor/transcripts/<skill>-<ts>.jsonl` (or iteration
dirs once #22 lands), redact secrets, attach path as `AssertionResult.evidence`,
`-v` prints relevant slice inline, document the schema.

**Why:** The agentskills.io spec flags transcript capture as table-stakes for
root-cause analysis. Without it, non-deterministic failures and token outliers
can't be diagnosed after the fact without a manual rerun.

### Codebase Findings — much of the ticket is already done

Tickets #21 (timing+tokens) and #22 (iteration workspaces) landed a
stream-json implementation that incidentally captured execution transcripts.
Current state:

**Already implemented:**
- `runner.py::_invoke` invokes `claude -p --output-format stream-json --verbose`
  and parses the NDJSON stream line-by-line
  (`src/clauditor/runner.py:192–363`).
- `SkillResult.stream_events: list[dict]` and `raw_messages: list[dict]` fields
  carry the full transcript in memory
  (`src/clauditor/runner.py:64–65`).
- `cli.py::_write_run_dir` writes `output.txt` + `output.jsonl` to each
  `run-K/` subdir inside the iteration workspace, one JSON object per line
  (`src/clauditor/cli.py:155–167`).
- `_cmd_grade_with_workspace` already invokes `_write_run_dir` for every
  primary + variance run under `iteration-N/<skill>/run-K/output.jsonl`
  (`src/clauditor/cli.py:560–561`). **Transcripts are persisted on every
  grade run, not opt-in.**
- Stream-json schema is documented in the `runner.py` module docstring
  (lines 3–26) as a **parser contract** (the shapes the runner relies on:
  `system` / `assistant{message.content[].text}` / `result{usage}`).
- `AssertionResult.evidence: str | None` field exists
  (`src/clauditor/assertions.py:29–56`) — currently holds matched text
  snippets, e.g. first 5 URLs, first 100 chars of a regex match.

**What the ticket still actually needs:**
1. **Opt-in/opt-out control** — transcripts are written unconditionally
   today. They are **tiny in the common case** (a skill invocation emits
   a few dozen stream-json lines, typically <100 KB) but can grow
   meaningfully with long tool-use chains. Question is whether to add an
   opt-out to suppress them for CI/bulk runs, or leave always-on.
2. **Assertion → transcript cross-reference** — `AssertionResult.evidence`
   captures *matched content*, not a file pointer. Failing assertions have
   no pointer back to the `run-K/output.jsonl` that produced the failing
   output. Needed: a new field (or convention) so a failure row says
   "see iteration-3/<skill>/run-0/output.jsonl".
3. **Secret redaction** — no redaction happens today. `output.jsonl`
   contains the raw stream; a skill that echoes `os.environ['OPENAI_KEY']`
   would leak it to disk. The ticket explicitly calls this out.
4. **`-v` prints transcript slice inline** — `cmd_grade` does not currently
   print any transcript content on failure. On `-v`/verbose with a failed
   assertion, dump the last N stream-json `assistant`/`tool_use` blocks
   inline so users get context without opening the file.
5. **Stream-json schema doc as a stable contract** — the docstring is
   good but lives in runner.py. Promote to a dedicated doc under
   `docs/` (or `.claude/rules/stream-json-schema.md`) so clauditor's
   dependency on the Anthropic CLI format is explicit and versioned.
6. **`cmd_validate` + `cmd_extract` transcript capture** — `cmd_validate`
   (`cli.py:52`) runs a skill and prints output; it does **not** stage a
   workspace and does **not** write `output.jsonl`. Decide whether #26
   extends transcript capture to validate/extract, or stays grade-only
   (where the iteration workspace already exists).
7. **Ticket's suggested `.clauditor/transcripts/<skill>-<ts>.jsonl` path
   is superseded** by #22's per-iteration layout. Transcripts should
   stay inside `iteration-N/<skill>/run-K/output.jsonl`. Call this out
   explicitly in the plan so the ticket's scope language doesn't mislead
   later readers.

### Convention Constraints

From `.claude/rules/`:

- **`sidecar-during-staging.md`** — any new per-iteration JSON artifact
  MUST be written inside `workspace.tmp_path` BEFORE `workspace.finalize()`.
  Applies to any new per-run redacted transcript files or summary files.
- **`json-schema-version.md`** — any new *persisted* JSON file that may
  evolve must carry `schema_version: 1` as its first key. `output.jsonl`
  is NDJSON (Anthropic CLI's format), so this rule does NOT apply to it —
  its schema is owned by upstream. Any NEW top-level sidecar we add
  (e.g. a per-run `transcript_meta.json`) would be subject to the rule.
- **`path-validation.md`** — applies if we add a user-authored redaction
  config (patterns file).
- **`subprocess-cwd.md`** — runner already follows the pattern.
- **`monotonic-time-indirection.md`** / **`llm-judge-prompt-injection.md`**
  / **`eval-spec-stable-ids.md`** / **`positional-id-zip-validation.md`**
  — not directly applicable.

Also: 80% coverage gate enforced; ruff clean; no TodoWrite / markdown TODOs
(use beads); `bd` for all task tracking; `asyncio_mode = "strict"`.

### Proposed Scope

Treat #26 as a **thin finishing layer** on top of the #22 workspace. The
big-ticket items are: redaction, assertion→transcript cross-refs, the
verbose inline printer, and a stable schema doc. The opt-out flag and
validate/extract coverage are design decisions that depend on the
answers below.

Ordering: redaction primitives → assertion cross-ref plumbing →
verbose inline slice printer → optional validate/extract expansion →
schema doc promotion → Quality Gate → Patterns & Memory.

---

## Scoping Questions

**Q1 — Transcript on/off control**
Today `output.jsonl` is written unconditionally inside every iteration run dir.
The ticket asks for an opt-in `--transcript` flag. Given the current state:
- **(A)** Leave always-on. Transcripts are small, already persisted, and
  the iteration workspace is the natural home. Close the ticket's "opt-in"
  scope as OBE. No flag added.
- **(B)** Add an opt-out `--no-transcript` flag for users who want to skip
  disk write entirely (CI / bulk variance runs). Default remains on.
- **(C)** Add a `--transcript={full,redacted,off}` 3-way flag where `off`
  skips disk write, `redacted` applies secret scrubbing (see Q3), and
  `full` is the current raw capture (for local debugging only).
- **(D)** Add opt-in `--transcript` per the ticket's original language —
  default OFF, explicit flag to enable. Note: this is a **breaking change**
  — tooling that already reads `run-K/output.jsonl` (audit, compare) may
  silently lose data. Require careful review.

**Q2 — Assertion → transcript cross-reference shape**
When an assertion fails on run-K, how does the failure row point at
`iteration-N/<skill>/run-K/output.jsonl`?
- **(A)** Add a new optional `AssertionResult.transcript_path: str | None`
  field (relative path from repo root). Populated only when the caller
  passes the workspace layout; otherwise `None`.
- **(B)** Overload the existing `AssertionResult.evidence: str | None`
  field to hold the path when no substantive match exists. Ambiguous
  and backwards-compatible.
- **(C)** Add a new `transcript_ref` field on `AssertionSet` (the
  container) instead of per-assertion. One pointer per run, applies to
  all failing assertions in that set.
- **(D)** (A) + (C): per-assertion optional path for richer UX (e.g.
  different tool-use slice per failure), plus a set-level fallback.

**Q3 — Secret redaction scope**
"Redact secrets / known sensitive patterns before persisting" — what
exactly do we redact?
- **(A)** Minimal: redact values of common env-var names (`*_KEY`,
  `*_TOKEN`, `*_SECRET`, `*_PASSWORD`) when they appear as JSON values
  in stream events. Built-in list, no user config.
- **(B)** Minimal + regex patterns: (A) plus a hardcoded set of
  high-signal regexes (`sk-[A-Za-z0-9]{20,}`, `ghp_[A-Za-z0-9]{20,}`,
  AWS key prefixes, `Bearer [A-Za-z0-9._-]{20,}`).
- **(C)** (B) plus a user-authored
  `.clauditor/redaction.json` (or field on `EvalSpec`) with additional
  regex patterns. Apply `path-validation.md` rules if loaded from spec.
- **(D)** (C) but opt-in: redaction only runs when the user passes
  `--redact` or sets a spec field. Default behavior is raw capture
  (explicit user contract).

**Q4 — Verbose inline slice**
`-v` on a failing grade should print transcript content inline. What
content and how much?
- **(A)** Last 5 `assistant` text blocks from the failed run's stream
  (usually captures the model's final reasoning).
- **(B)** All `tool_use` blocks from the failed run (captures *what the
  skill did*, which is the root-cause-analysis case the ticket names).
- **(C)** (A) + (B) interleaved in chronological order, capped at N KB
  total.
- **(D)** Dump the full `output.jsonl` contents up to a cap, with a
  truncation marker. Simplest; lets the user grep themselves.

**Q5 — `cmd_validate` and `cmd_extract` transcript capture**
Today only `cmd_grade` persists transcripts (because only grade stages
a workspace). Should validate/extract also?
- **(A)** Keep grade-only. validate/extract stay free-form, no workspace.
  Users needing transcripts run `grade`.
- **(B)** Extend workspace staging to `cmd_validate` so it also writes
  `run-0/output.jsonl` (under a different iteration-or-transient dir).
  Larger lift — touches #22 workspace API.
- **(C)** `cmd_validate` + `cmd_extract` write a lightweight transcript
  to `.clauditor/transcripts/<skill>-<ts>.jsonl` (the ticket's original
  path), bypassing iteration workspaces entirely. Two parallel layouts.
- **(D)** (A) now, file a follow-up ticket for validate/extract if users
  ask for it.

**Q6 — Stream-json schema doc home**
Where does the "clauditor depends on this stream-json shape" contract
live?
- **(A)** Leave it in `runner.py`'s module docstring (current state).
  Close this scope item as already done.
- **(B)** Promote to `docs/stream-json-schema.md` — a user-facing doc
  that external contributors can read without opening source.
- **(C)** Promote to `.claude/rules/stream-json-schema.md` — an
  AI-agent-facing convention rule that future edits must respect.
- **(D)** (B) + (C): user doc for humans, rule for agents, both
  generated from a single source.

---

## Scoping Answers (2026-04-14)

- **Q1 = B** — Opt-out `--no-transcript` flag; default stays on.
- **Q2 = A** — New `AssertionResult.transcript_path: str | None` field.
- **Q3 = B** — Hardcoded env-var names + known-prefix regexes. No user config.
- **Q4 = A** — `-v` prints last 5 `assistant` text blocks from the failed run.
- **Q5 = B** — Extend workspace staging to `cmd_validate`.
- **Q6 = D** — Publish both `docs/stream-json-schema.md` and
  `.claude/rules/stream-json-schema.md`.

Note from user: library is unpublished, no back-compat constraints. Free to
rename/add fields cleanly.

---

## Architecture Review

| Area | Rating | Findings |
|---|---|---|
| Security | **concern** | Redaction correctness is load-bearing |
| Performance | pass | Transcripts are <100 KB typical; validate workspace adds trivial overhead |
| Data Model | **concern** | `cmd_validate` workspace sharing `iteration-N` with `cmd_grade` needs a clean design |
| API Design | pass | `--no-transcript` on both `grade` and `validate` is mechanical |
| Observability | **concern** | Redaction count should be visible under `-v` |
| Testing Strategy | pass | Clear unit + integration test shape |
| Rules Compliance | pass | No new JSON sidecars; `sidecar-during-staging` covers transcript writes |

### Security — concern (not blocker)

Redaction runs *before* disk write, in one place: a new
`clauditor.transcripts.redact()` helper that walks the `stream_events`
structure and scrubs string values matching:

- Env-var name heuristic: any JSON key ending in `_KEY`, `_TOKEN`,
  `_SECRET`, `_PASSWORD`, `_PASSPHRASE`, `_CREDENTIAL`, or exactly
  `API_KEY` / `AUTH` (case-insensitive). Value replaced with `[REDACTED]`.
- Regex heuristics on string values (any nesting depth):
  - `sk-(ant|proj|live|test)?[-_]?[A-Za-z0-9]{20,}` (OpenAI / Anthropic)
  - `ghp_[A-Za-z0-9]{36,}` and `github_pat_[A-Za-z0-9_]{80,}` (GitHub)
  - `AKIA[0-9A-Z]{16}` and `ASIA[0-9A-Z]{16}` (AWS)
  - `Bearer\s+[A-Za-z0-9._\-]{20,}` (generic auth headers)
  - `xox[abprs]-[A-Za-z0-9-]{10,}` (Slack)

Concerns to watch:
- The `args` field on `SkillResult` — if a user passes a secret as a CLI
  arg, it is embedded in the prompt string and lands in the transcript.
  `redact()` must run on the textual prompt too, not just stream events.
- `raw_messages` carries the same data as `stream_events` plus extras.
  `_write_run_dir` only persists `stream_events`, so that is the only
  stream that needs scrubbing on disk.
- Redaction is a walk-and-replace on JSON, not on the serialized line.
  This avoids double-escaping issues and keeps the NDJSON shape valid.

### Data Model — concern (needs refinement decision)

Today `cmd_validate` writes nothing to disk. Option Q5=B extends it to
stage a workspace. Two possible layouts:

- **(L1)** Share iteration-N with grade. A `validate` run bumps the
  iteration counter, making `iteration-3/<skill>/` ambiguous: was it
  written by grade or validate? `history.jsonl` would need a `command`
  discriminator (already exists for grade).
- **(L2)** Separate counter: `validate-iteration-N/`. Two parallel trees
  under `.clauditor/`. Cleaner separation; slightly more machinery.
- **(L3)** Transient: `validate` writes to a temp dir, prints the path,
  does not publish. Root-cause analysis works; no longitudinal data. No
  conflict with iteration layout.

Refinement phase must pick one. **Default recommendation: L3** —
validate is interactive/iterative by nature; users don't need a
longitudinal history of validate runs, only the latest transcript for
debugging. L3 is also the smallest lift.

### Observability — concern (easy fix)

Redaction should log a count under `-v` so users know scrubbing
happened: `clauditor.transcripts: redacted 3 matches in run-0`. Silent
redaction breeds distrust; users need to verify redaction is actually
running.

### Rules Compliance

- `sidecar-during-staging.md` — all transcript writes already land in
  `workspace.tmp_path` before `finalize()`; extending this to
  `cmd_validate` (if L1/L2) must preserve the rule. L3 (temp dir) is
  exempt because there is no publication event.
- `json-schema-version.md` — `output.jsonl` is NDJSON owned by Anthropic
  CLI; no version header. No new clauditor-owned JSON sidecar planned.
- `path-validation.md` — not applicable (Q3=B, no user config).
- `subprocess-cwd.md` — runner already compliant.

## Refinement Log

### Decisions

- **DEC-001** — Opt-out `--no-transcript` flag; transcripts remain on by
  default. (Q1=B)
- **DEC-002** — Add new `AssertionResult.transcript_path: str | None`
  field. Do not overload `evidence`. (Q2=A; library unpublished, free
  to add.)
- **DEC-003** — Redaction is a hardcoded env-var-name heuristic + known-
  prefix regex set living in a new `clauditor/transcripts.py` module. No
  user config file, no `EvalSpec` field. (Q3=B)
- **DEC-004** — On `-v` with a failing grade, print the last 5
  `assistant` text blocks from the failed run's `stream_events`.
  Fewer-than-5 is fine. (Q4=A)
- **DEC-005** — Extend workspace staging to `cmd_validate` so it writes
  `run-0/output.jsonl` alongside assertion results. (Q5=B)
- **DEC-006** — Stream-json schema contract is published to both
  `docs/stream-json-schema.md` (human-facing) and
  `.claude/rules/stream-json-schema.md` (agent-facing rule). The
  existing `runner.py` module docstring stays as the in-code pointer.
  (Q6=D)
- **DEC-007** — Redaction is **mandatory**. No `--no-redact` escape
  hatch. A user who needs raw capture must delete redaction regex
  entries in source. (R1=A)
- **DEC-008** — `cmd_validate` shares the `iteration-N/<skill>/`
  counter with `cmd_grade`. Disambiguation happens via the existing
  `history.append_record(command=...)` discriminator (already has
  `"grade"` / `"validate"` slots in `history.py`). Both commands can
  publish to the same iteration dir because the file layouts do not
  collide: grade writes `grading.json` + `timing.json` +
  `assertions.json`; validate writes only `assertions.json` + run dirs.
  (R2=A)
- **DEC-009** — Redaction count is always logged under `-v` as
  `clauditor.transcripts: redacted N matches in run-K` — even when
  `N == 0`, so silent-redaction trust concerns don't resurface. (R3=A)
- **DEC-010** — `redact()` scrubs both `stream_events` (the disk
  payload) and the prompt text / `args` field, since a user may pass a
  secret on the CLI. Scrubbing happens once, in `_write_run_dir`,
  immediately before `json.dumps`. The in-memory `SkillResult` is not
  mutated — only the serialized form.
- **DEC-011** — `cmd_validate`'s new history record uses
  `command="validate"` so `clauditor trend` can filter/group correctly.
  `pass_rate` maps to assertion pass rate; `mean_score` is omitted (no
  Layer 3 grader in validate's path).
- **DEC-012** — `cmd_extract` is **out of scope** for this ticket.
  File a follow-up if users ask for transcript capture in extract
  flows. Scope-line answer: Q5=B said "validate", not "extract".

### Session Notes

Discovery revealed #26 is 60% done incidentally via #21 + #22: transcripts
are already captured in memory and persisted as `output.jsonl` inside
iteration workspaces. The remaining work is redaction, assertion
cross-references, the verbose slice printer, `cmd_validate` workspace
extension, and the schema contract docs. The library being unpublished
unblocks adding the `AssertionResult.transcript_path` field without
ceremony.

---

## Detailed Breakdown

Ordering: pure-logic primitive first (redaction module, TDD), then
schema additions, then wire-up into the two commands, then the verbose
slice, then docs, then quality gate + patterns.

### US-001 — Redaction module (`clauditor/transcripts.py`)

**Description:** New module `src/clauditor/transcripts.py` exposing
`redact(obj: Any) -> tuple[Any, int]` — walks a JSON-compatible value
recursively and returns `(scrubbed_copy, count)` where `count` is the
number of replacements made. Pure logic; no I/O.

**Traces to:** DEC-003, DEC-007, DEC-010.

**Acceptance criteria:**
- `redact()` replaces values of dict keys matching (case-insensitive)
  `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `*_PASSPHRASE`,
  `*_CREDENTIAL`, exact `AUTH` / `API_KEY` → `"[REDACTED]"`.
- Scans string values (any nesting depth) against this regex set and
  replaces **the matched span only**, not the whole string:
  `sk-(ant|proj|live|test)?[-_]?[A-Za-z0-9]{20,}`, `ghp_[A-Za-z0-9]{36,}`,
  `github_pat_[A-Za-z0-9_]{80,}`, `AKIA[0-9A-Z]{16}`, `ASIA[0-9A-Z]{16}`,
  `Bearer\s+[A-Za-z0-9._\-]{20,}`, `xox[abprs]-[A-Za-z0-9-]{10,}`.
- Does not mutate input; returns a new structure.
- `count` is the total number of replacements (both key-based and
  regex-match-based) across the whole walk.
- `uv run ruff check src/ tests/` clean.
- `uv run pytest tests/test_transcripts.py -v` passes.
- Module-level docstring names each regex and links to the rule file.

**Done when:** module published, 100% unit test coverage on the
redaction logic, all tests green.

**Files:**
- `src/clauditor/transcripts.py` (new)
- `tests/test_transcripts.py` (new)

**Depends on:** none

**TDD cases (write first):**
- env-var key scrub: `{"OPENAI_API_KEY": "abc"}` → `{"OPENAI_API_KEY": "[REDACTED]"}`, count=1
- case-insensitive: `{"openai_api_key": "abc"}` scrubbed identically
- nested dict: `{"env": {"GITHUB_TOKEN": "ghp_..."}}` scrubbed at depth
- list traversal: `[{"API_KEY": "x"}]` scrubbed
- OpenAI key regex: `"my key is sk-proj-abcdefghijklmnopqrstuv"` →
  `"my key is [REDACTED]"`, count=1
- GitHub PAT regex positive + negative (too short)
- AWS AKIA regex positive
- Bearer regex positive (with space)
- Slack xoxb regex positive
- Mixed: dict with key scrub AND nested string with regex scrub →
  count=2
- Non-match passthrough: plain text is untouched
- Scrubs the matched span only: `"prefix sk-live-xxxxxxxxxxxxxxxxxxxx suffix"`
  → `"prefix [REDACTED] suffix"`
- Count=0 when no matches
- None / int / bool values left alone
- Returns new object, original unchanged (deep-equality check before
  and after)

---

### US-002 — `AssertionResult.transcript_path` field

**Description:** Add a new optional `transcript_path: str | None = None`
field to `AssertionResult` and carry it through `to_json_dict` /
`from_json_dict` and the `AssertionSet` container.

**Traces to:** DEC-002.

**Acceptance criteria:**
- `AssertionResult` dataclass gets `transcript_path: str | None = None`
  as a new keyword-default field.
- `to_json_dict` emits `"transcript_path": ...` (even when `None`, for
  schema stability).
- `from_json_dict` accepts the key and threads it through (missing-key
  tolerant for older fixtures).
- `AssertionSet.summary()` / `__str__` representation unchanged.
- Existing `assertions.json` writers set `transcript_path=None`
  (wire-up happens in US-004).
- Existing tests still pass unmodified.
- New tests cover: round-trip with path set, round-trip with path
  absent, default is None.

**Done when:** `uv run pytest tests/test_assertions.py -v` green,
existing assertions sidecars continue to round-trip.

**Files:**
- `src/clauditor/assertions.py` (edit `AssertionResult`, `run_assertions`
  plumbing, `to_json_dict` / `from_json_dict`)
- `tests/test_assertions.py` (new round-trip tests)

**Depends on:** none

**TDD cases:**
- Round-trip: `AssertionResult(..., transcript_path="foo.jsonl")` →
  `to_json_dict()` → `from_json_dict()` → equal path
- Default: `AssertionResult(...).to_json_dict()["transcript_path"] is None`
- Back-compat: old fixture without the key loads with
  `transcript_path=None`

---

### US-003 — Wire redaction into `_write_run_dir`

**Description:** In `cli.py::_write_run_dir`, call
`transcripts.redact()` on the `stream_events` list before serializing,
and on the output_text (in case a skill echoes a key in its final
answer). Log the redaction count under `-v`.

**Traces to:** DEC-003, DEC-007, DEC-009, DEC-010.

**Acceptance criteria:**
- `_write_run_dir` takes an additional `verbose: bool = False`
  parameter (threaded from the CLI flag).
- Calls `redact(stream_events)` and `redact({"output": output_text})`
  (or similar) before writing; both returned counts are summed.
- Under `verbose=True`, prints
  `clauditor.transcripts: redacted N matches in <run_dir.name>` to
  stderr, **always**, including when `N == 0`.
- `output.jsonl` on disk contains the scrubbed form; in-memory
  `stream_events` in `SkillResult` is untouched (DEC-010).
- `output.txt` also contains the scrubbed form.
- Does not write anything if `--no-transcript` was passed (US-004
  wires the flag).
- Integration test: grade a fake skill whose stream output contains a
  `sk-proj-...` token; assert the token is absent from `output.jsonl`
  and the redaction log line is printed.

**Done when:** integration test passes, ruff clean, coverage ≥80% on
touched lines.

**Files:**
- `src/clauditor/cli.py` (`_write_run_dir`, threading `verbose` from
  callers)
- `tests/test_cli_transcript_redaction.py` (new)

**Depends on:** US-001

---

### US-004 — Thread `transcript_path` into grade's assertion results

**Description:** In `_cmd_grade_with_workspace`, after staging each
`run-K/output.jsonl`, populate the per-run `AssertionResult.transcript_path`
with the repo-relative path to that `output.jsonl`. Applies to every
assertion in the run, failing or passing (simpler, cheaper than
computing per-assertion pointers).

**Traces to:** DEC-002, DEC-011.

**Acceptance criteria:**
- `run_assertions(text, eval_spec.assertions)` is replaced with a
  wrapper that post-processes the `AssertionSet` to set
  `transcript_path` on every `AssertionResult`.
- Path is computed via the existing `_relative_to_repo` helper
  (`cli.py:709`) applied to `run_dir / "output.jsonl"`.
- `--no-transcript` suppresses the path (set to `None`) since the file
  won't exist.
- `assertions.json` on disk carries the path per result.
- When `_cmd_grade_with_workspace` is called with `args.output`
  (captured text, no run), `transcript_path` is `None` for every
  result (no stream to point at).
- Existing grade tests continue to pass.

**Done when:** running `clauditor grade <skill>` on a real fixture
produces an `assertions.json` where failing rows carry a non-null
`transcript_path` pointing at the actual on-disk file.

**Files:**
- `src/clauditor/cli.py` (`_cmd_grade_with_workspace` assertion block
  around `:567–584`)
- `tests/test_cli.py` (update fixture expectations)

**Depends on:** US-002, US-003

---

### US-005 — `--no-transcript` opt-out flag

**Description:** Add `--no-transcript` to both `clauditor grade` and
`clauditor validate` argparse subparsers. When set, `_write_run_dir`
is not called; `assertions.json` rows get `transcript_path=None`.

**Traces to:** DEC-001.

**Acceptance criteria:**
- `cmd_grade` / `_cmd_grade_with_workspace` honor `args.no_transcript`.
- `cmd_validate` honors it too (once US-006 lands the workspace).
- Help text: `--no-transcript  Skip writing per-run stream-json transcripts`
- `assertions.json` still lands, but its entries have `transcript_path=None`
  and no `run-K/output.jsonl` / `output.txt` exist.
- Grade still produces `grading.json` / `timing.json` unchanged.
- Test: grade with `--no-transcript` → assert no `run-0/output.jsonl`
  exists, `assertions.json` transcript_path is None, exit code matches
  the run-with-transcripts case.

**Done when:** flag works on both commands, test covers both.

**Files:**
- `src/clauditor/cli.py` (argparse additions + branch in
  `_cmd_grade_with_workspace`)
- `tests/test_cli.py`

**Depends on:** US-004

---

### US-006 — Extend workspace staging to `cmd_validate`

**Description:** `cmd_validate` currently runs the skill and prints
output with no persistence. Wrap it in the same `allocate_iteration` /
`workspace.tmp_path` / `workspace.finalize()` flow used by grade, so
it writes `run-0/output.jsonl`, `run-0/output.txt`, and
`assertions.json` (with `transcript_path` wired via US-004). Share the
`iteration-N` counter with grade. Add history record with
`command="validate"`.

**Traces to:** DEC-005, DEC-008, DEC-011.

**Acceptance criteria:**
- `cmd_validate` uses `allocate_iteration(...)` and
  `workspace.finalize()` / `workspace.abort()` on exception, per
  `.claude/rules/sidecar-during-staging.md`.
- Written artifacts: `iteration-N/<skill>/run-0/output.jsonl`,
  `.../run-0/output.txt`, `.../assertions.json`. No `grading.json`,
  no `timing.json` (validate has no Layer 3).
- Respects `--no-transcript` from US-005.
- Appends to `history.jsonl` with `command="validate"`, `pass_rate`
  from the assertion set, `mean_score` omitted (or `None`).
- Existing validate behavior (stdout print of skill output, exit code
  on failure) unchanged.
- Test: `clauditor validate <skill>` creates an iteration dir, writes
  the expected files, appends the correct history row.

**Done when:** `clauditor validate` fixture test publishes an iteration,
`history.jsonl` grows by one `command="validate"` row, transcript is
present under the iteration dir.

**Files:**
- `src/clauditor/cli.py` (`cmd_validate` rewrite)
- `src/clauditor/history.py` (verify `command="validate"` acceptance;
  likely already supported per DEC-008)
- `tests/test_cli.py`

**Depends on:** US-003, US-004, US-005

---

### US-007 — Verbose `-v` transcript slice printer

**Description:** When `-v` / `--verbose` is set AND any assertion fails
on a run, print the last 5 `assistant` text blocks from that run's
`stream_events` to stderr, labeled with the run index. Fewer-than-5
available is fine — print what exists.

**Traces to:** DEC-004.

**Acceptance criteria:**
- New helper `clauditor.cli::_print_failing_transcript_slice(run_idx,
  stream_events, out)` — pure function, testable.
- Extracts `msg.get("message", {}).get("content", [])` from each
  `assistant` event, filters to `block.get("type") == "text"`, keeps
  the last 5 (across all assistant events in order).
- Header line: `--- transcript slice (run-K, last 5 assistant blocks) ---`
- Prints block texts separated by blank lines, capped at 2 KB per
  block (truncation marker `... [truncated]` on overflow).
- Invoked from `_cmd_grade_with_workspace` only when `args.verbose` is
  true **and** `AssertionSet.failed()` is non-empty for that run.
- Works for `cmd_validate` too (same call site pattern, once US-006
  lands).
- Redaction has already run on the in-memory `stream_events`? No —
  in-memory is untouched per DEC-010. Run `redact()` on the slice
  before printing. (One more small call; cheap.)
- Test: synthetic `stream_events` with 8 assistant text blocks →
  printer emits the last 5, in order, each under cap.
- Test: fewer than 5 blocks → printer emits what's available.
- Test: slice contains a fake token → token is redacted in the
  printed output.

**Done when:** tests pass; manual run on a failing skill shows the
slice.

**Files:**
- `src/clauditor/cli.py` (new helper + call sites)
- `tests/test_cli_transcript_slice.py` (new)

**Depends on:** US-001 (for redaction), US-006 (to invoke from validate)

---

### US-008 — Stream-json schema contract docs

**Description:** Promote the parser contract currently in
`runner.py`'s module docstring to two published files:
`docs/stream-json-schema.md` (human-readable, with examples) and
`.claude/rules/stream-json-schema.md` (agent-facing rule in the
established `.claude/rules/` format: pattern, why, canonical
implementation).

**Traces to:** DEC-006.

**Acceptance criteria:**
- `docs/stream-json-schema.md` documents every message shape
  clauditor parses: `system`, `assistant{message.content[].text|tool_use}`,
  `result{usage{input_tokens,output_tokens}}`. Notes which fields are
  *required*, which are tolerated-if-missing, and how malformed lines
  are handled (skip + stderr warning).
- `.claude/rules/stream-json-schema.md` follows the rule-file shape
  (Rule: …, The pattern, Why this shape, Canonical implementation:
  `src/clauditor/runner.py::_invoke`).
- `runner.py` module docstring is shortened to a pointer at both docs
  (don't delete it; it's load-bearing for in-code navigation).
- README or top-level docs index links to `docs/stream-json-schema.md`
  if an index exists (grep for a docs index first).

**Done when:** both files exist, `runner.py` points at them.

**Files:**
- `docs/stream-json-schema.md` (new)
- `.claude/rules/stream-json-schema.md` (new)
- `src/clauditor/runner.py` (docstring trim)

**Depends on:** none

---

### US-009 — Quality Gate

**Description:** Run the code reviewer 4 times across the full
changeset, fix every real bug found each pass. Run CodeRabbit if
available. Project validation (`uv run ruff check src/ tests/` +
`uv run pytest --cov=clauditor --cov-report=term-missing` with 80%
gate) must pass after all fixes.

**Acceptance criteria:**
- Code-reviewer pass 1–4 executed; each pass's real findings fixed
  (false positives documented in the bead notes).
- `uv run ruff check src/ tests/` clean.
- `uv run pytest --cov=clauditor --cov-report=term-missing` green,
  coverage ≥ 80% global and ≥ 80% on touched files.
- CodeRabbit findings (if PR is up) addressed or explicitly
  documented as false positive per `pr-reviewer` conventions.

**Done when:** all four review passes complete, validation green.

**Depends on:** US-001 … US-008

---

### US-010 — Patterns & Memory

**Description:** Capture any new patterns that emerged during
implementation. Candidates:
- Redaction walk pattern (if novel) → new `.claude/rules/` file
- "Share iteration counter across multiple commands" pattern (if the
  validate workspace sharing proved subtle) → rule
- Auto-memory entries for any user preferences captured in this
  session (none expected; not pre-published library → no major
  preference shifts).

**Acceptance criteria:**
- Review all `.claude/rules/` additions the plan touched; verify none
  were skipped.
- If any implementation step surfaced a non-obvious convention, add it
  as a new rule file or extend an existing one.
- `bd remember` calls for persistent insights worth keeping across
  sessions.

**Depends on:** US-009

---

## Beads Manifest
_pending devolve_
