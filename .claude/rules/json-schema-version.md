# Rule: Every new JSON artifact declares and validates `schema_version`

Any new persisted JSON file (sidecar, report, history record) must include a
`schema_version: 1` field as the first top-level key, and every loader that
reads the file must verify the version before consuming it. Forward-compat
silence — where a v1 loader silently misparses a v2 file — is a recipe for
subtle history corruption that only surfaces weeks later.

## The pattern

### Writer

```python
def to_json(self) -> str:
    payload = {
        "schema_version": 1,  # first key — canonical diff stability
        "skill_name": self.skill_name,
        ...
    }
    return json.dumps(payload, indent=2) + "\n"
```

### Loader

```python
_SCHEMA_VERSION = 1

def _check_schema_version(data: dict, source: Path) -> bool:
    version = data.get("schema_version")
    if version != _SCHEMA_VERSION:
        print(
            f"warning: {source} has schema_version={version!r}, "
            f"expected {_SCHEMA_VERSION} — skipping",
            file=sys.stderr,
        )
        return False
    return True


def _records_from_sidecar(data: dict, source: Path) -> list[Record]:
    if not _check_schema_version(data, source):
        return []
    # ... parse records ...
```

## Why this shape

- **`schema_version` as first key**: canonical diff stability. Humans and
  tools reading the file see the version immediately; a bump is visually
  obvious in a diff.
- **Hard numeric comparison, not substring**: `version != 1` catches
  missing version, wrong type (string `"1"`), or any future bump. No
  accidental soft-match on `"v1.0"`.
- **Skip + log, do not crash**: mismatched-version files are skipped with a
  stderr warning. The caller (e.g. `load_iterations`) tolerates empty
  results, so one bad file does not take down an entire audit run.
- **Pre-release, so version 1 is the starting point**: when a future bump
  ships, writers emit `schema_version: 2` and loaders are updated to accept
  `{1, 2}` with a per-version parser. This pattern makes the bump explicit
  and auditable.

## Canonical implementation

Writers: `src/clauditor/quality_grader.py::GradingReport.to_json`,
`src/clauditor/grader.py::ExtractionReport.to_json`,
`src/clauditor/audit.py::render_json`,
`src/clauditor/baseline.py::BaselineReports.to_json_map`, and the
sidecar envelopes in `src/clauditor/cli.py::_cmd_grade_with_workspace`.

Loaders: `src/clauditor/audit.py::_check_schema_version` and its call sites
in `_records_from_assertions`, `_records_from_extraction`,
`_records_from_grading`.

### Schema version bumps for #86 (`transport_source` field)

#86 added `transport_source` to `GradingReport` and `ExtractionReport` to
record which Anthropic backend (`"api"` or `"cli"`) handled each grader call.
Rather than creating a new sidecar format, the existing `grading.json` and
`extraction.json` sidecars were bumped to `schema_version: 2`. The audit
loader (`_check_schema_version`) was updated to accept `{1, 2}` and defaults
`transport_source` to `"api"` when reading v1 sidecars so pre-#86 history
stays readable without reprocessing.

`assertions.json` sidecars were NOT bumped — L1 assertions make no Anthropic
call and have no transport to record.

### Schema version bumps for #147 (`provider_source` field)

`#147` added `provider_source` to `GradingReport` and `ExtractionReport` to
record which model provider's SDK (`"anthropic"` or `"openai"`) served each
grader call — the provider-axis sibling of `transport_source`. The field
name is deliberately parallel to `transport_source` per DEC-001 of
`plans/super/147-sidecar-provider-field.md`. The existing `grading.json`
and `extraction.json` sidecars bumped from `schema_version: 2` to
`schema_version: 3`; the audit loader accepts `{1, 2, 3}` and defaults
`provider_source` to `"anthropic"` when reading legacy v1/v2 sidecars so
pre-#147 history stays readable without reprocessing.

In parallel, `history.jsonl` (the per-iteration JSONL stream
`clauditor trend` reads) bumped from `schema_version: 1` to
`schema_version: 2` per DEC-012, adding a top-level `provider` field to
each record. `history.append_record` is now keyword-only on `provider=`
(both call sites — `cli/grade.py` and `cli/extract.py` — pass the
resolved provider). `read_records` defaults missing `provider` to
`"anthropic"` for legacy v1 lines so the trend mixed-provider refusal
(DEC-011) works without rewriting old history.

`assertions.json` was deliberately NOT bumped per DEC-002 — L1 assertions
make no LLM call and have no provider to record. The honest harness-axis
bump for `assertions.json` (and the next `grading.json`/`extraction.json`
revision adding a `harness` field) lives in #152, which is strictly
separable from #147 per DEC-006. `IterationRecord` carries a
`provider: str = "anthropic"` placeholder so audit groups L1 records under
`("anthropic", "L1", id)` regardless of the underlying skill harness — a
small lie in mixed-harness scenarios that #152 will resolve.

DEC-008 refactored the audit loader's accepted-version surface from a
per-filename `frozenset` (the #86-vintage shape) to a `MAX_SCHEMA_VERSION:
dict[str, int]` map plus a pure helper `_is_accepted_version(filename,
version)` that enforces `1 <= version <= MAX_SCHEMA_VERSION[base]`
(stripping the `baseline_` prefix). Future bumps (e.g. #152's `harness`
field) become a one-number-per-file edit instead of re-listing the
accepted set. The "version-and-up" check assumes monotonic forward
compatibility within a sidecar family — per-version shape differences
remain the responsibility of the per-version `_records_from_*` helpers.
File anchors: `src/clauditor/audit.py::MAX_SCHEMA_VERSION` (the canonical
map) + `src/clauditor/audit.py::_is_accepted_version` (the pure helper)
+ `src/clauditor/audit.py::_check_schema_version` (the loader-side
caller that emits the stderr warning).

`history.py` keeps its own narrow accept-list
`_ACCEPTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1, 2})` rather
than reusing the audit `MAX_SCHEMA_VERSION` map — the JSONL stream is a
different artifact family with its own version lifecycle, and
duplicating two integers is cheaper than coupling the two surfaces.
File anchor: `src/clauditor/history.py::SCHEMA_VERSION` +
`_ACCEPTED_SCHEMA_VERSIONS`. If a third sidecar family adopts the same
"max version per filename" shape, extract a shared helper module per
DEC-008's pattern; today two call sites is below the
extraction threshold.

Writers and readers (post-#147):

- `src/clauditor/quality_grader.py::GradingReport.to_json` /
  `GradingReport.from_json` — emits `schema_version: 3`; reader
  defaults missing `provider_source` to `"anthropic"`.
- `src/clauditor/grader.py::ExtractionReport.to_json` /
  `ExtractionReport.from_json` — same shape.
- `src/clauditor/history.py::append_record` (keyword-only `provider=`) /
  `read_records` (defaults missing `provider` to `"anthropic"`).

### New sidecar family — `context.json` (#154)

`#154` introduced `context.json` — a new per-iteration sidecar family
written once per `iteration-N/<skill>/` alongside `assertions.json`,
`extraction.json`, and `grading.json`. It carries the comparability
metadata that lets `clauditor audit` / `clauditor trend` group runs
honestly across the harness, provider, model, and sandbox axes
(rather than bumping every existing per-call sidecar with
correlated fields). The file ships at `schema_version: 1` and is
**designed to stay at v1** — see the always-v1 contract below.

The dataclass `src/clauditor/context.py::IterationContext` is the
single source of truth for the v1 shape:

| Field                  | Type           | Nullability                                    |
| ---------------------- | -------------- | ---------------------------------------------- |
| `schema_version`       | `int = 1`      | always non-null (first key per the invariant)  |
| `harness`              | `str`          | always non-null — `{"claude-code", "codex"}`   |
| `provider`             | `str \| None`  | null when no LLM grading happened — `{"anthropic", "openai"}` when set |
| `model_runner`         | `str \| None`  | null when the harness cannot expose the actually-used model (e.g. the `claude` CLI's stream-json `result` carries no model field, so `ClaudeCodeHarness` records `None` when neither the constructor nor the per-call override pinned a value); fabricating a default would be a lie |
| `model_grader`         | `str \| None`  | null iff `provider` is null                    |
| `system_prompt_source` | `str`          | always non-null — `{"explicit", "agents_md", "skill_md"}` |
| `sandbox_mode`         | `str \| None`  | null when `harness != "codex"` — `{"read-only", "workspace-write", "danger-full-access"}` when set |
| `reasoning_tokens`     | `int \| None`  | always null in this PR — populated by #170     |
| `cost_usd`             | `float \| None`| always null in this PR — populated by #169     |

Registered in the audit loader's per-filename version map as
`MAX_SCHEMA_VERSION["context.json"] = 1` in
`src/clauditor/audit.py::MAX_SCHEMA_VERSION`, sharing the same
`_is_accepted_version` / `_check_schema_version` plumbing every
other sidecar family uses (DEC-008's "version-and-up" check).

**Always-v1 contract — forward-compat-by-design.** Unlike
`grading.json` / `extraction.json` (which bumped at #86 and #147
to add new non-null fields), `context.json` is engineered so the
two anticipated follow-ups can populate fields **without bumping
the schema version**. The trick: `reasoning_tokens` and `cost_usd`
ship as nullable from day one, even though no production writer
populates them yet. A null field is an absent value, not a
structural change — so when #169 wires up the pricing module to
fill `cost_usd: float` and #170 wires up per-provider reasoning
capture to fill `reasoning_tokens: int`, the on-disk shape stays
v1 and every existing reader keeps working unchanged. Bumps cost
audit / trend / badge integration churn (default-on-read defaults,
loader branches, regression tests for legacy iterations); avoiding
them by pre-declaring the fields as nullable is the cheaper shape
when the future fields are known up-front. This is the key teaching
this subsection codifies: **a new sidecar family that anticipates
follow-up additions should pre-declare them as nullable in v1
rather than ship a minimal v1 and bump on every addition.**

Writers (both write the same `IterationContext.to_json()` payload
into `skill_dir / "context.json"` during workspace staging per
`.claude/rules/sidecar-during-staging.md`):

- `src/clauditor/cli/grade.py::_write_workspace_sidecars` — the
  `grade` command's per-iteration write site.
- `src/clauditor/cli/validate.py::cmd_validate` — the `validate`
  command's per-iteration write site.

Readers:

- `src/clauditor/audit.py::_read_context` — audit-side reader,
  invoked from `load_iterations` parallel to the per-record
  loaders. Iterations whose `context.json` is absent (pre-#154
  history) map to `None` so renderers can emit a nullable column.
- `src/clauditor/badge.py::load_iteration_context` — badge-side
  reader that surfaces the parsed `IterationContext` into
  `ClauditorExtension.context` (the optional carrier field on the
  badge sidecar's clauditor-extension payload, omitted entirely
  when `None` per the layer-omission shape — see
  `.claude/rules/dual-version-external-schema-embed.md`).

Follow-up tickets that will populate the nullable placeholders
WITHOUT bumping the schema: #169 (`cost_usd` pricing module) and
#170 (`reasoning_tokens` per-provider capture).

**Audit-output JSON bump (`render_json` v3 → v4).** Independent of
the per-sidecar `MAX_SCHEMA_VERSION["context.json"]: 1`
registration above, the audit-output JSON envelope itself bumped
from `schema_version: 3` to `schema_version: 4` in #154 US-005
(DEC-005) when the new top-level `iteration_contexts` array
landed. The bump follows the same shape as the #147 v1→v2 bump
and the #152 v2→v3 bump: a new top-level field is a SHAPE change
that demands a `schema_version` increment so downstream JSON
consumers have a stable signal to branch on. v3 readers should
expect the `iteration_contexts` field to be absent; v4 readers
can treat `iteration_contexts == []` as "no contexts available"
(the legacy-iterations case). File anchor:
`src/clauditor/audit.py::render_json`.
### Schema version bumps for #152 (`harness` field)

`#152` added `harness` — the harness-axis sibling to `provider_source` —
recording which harness CLI (`"claude-code"` or `"codex"` today) ran the
skill subprocess. The harness identity is materialized once by #151's
resolver, stamped onto `SkillResult.harness` at `SkillRunner._invoke`
construction time (US-001), and threaded into every downstream sidecar
emitter from there. The field name deliberately omits the `_source`
suffix that `provider_source` carries: `provider_source` records which
provider backend served the grader call, while `harness` records what
materially ran the skill — they are conceptually distinct, and the name
keeps them visually distinguishable on disk per DEC-001 of
`plans/super/152-sidecar-harness-field.md`.

Five schema bumps shipped together in #152:

- **`assertions.json` v1 → v2** — adds top-level `harness` field
  (sibling-of-`schema_version` placement per DEC-003). Unlike
  `extraction.json` / `grading.json`, the L1 sidecar previously had no
  provider/harness axis at all (L1 makes no LLM call) — #152 is the
  first bump on this file.
- **`extraction.json` v3 → v4** — adds `harness` sibling to the
  existing `provider_source` field (DEC-004).
- **`grading.json` v3 → v4** — same shape as `extraction.json`
  (DEC-004).
- **`history.jsonl` v2 → v3** — adds top-level `harness` field per
  record. `harness=` becomes a mandatory keyword-only parameter on
  `append_record` (mirroring the post-#147 `provider=` mandatory-
  keyword shape). `read_records` defaults missing `harness` to
  `"claude-code"` for legacy v1/v2 lines so pre-#152 history stays
  readable. Per DEC-005, the history bump lands in lockstep with the
  sidecar bumps so #153 can refuse mixed-harness history at `trend`
  time without a follow-up version skew.
- **audit `render_json` output v2 → v3** — adds top-level
  `harnesses_seen: list[str]` array (sorted, deduped) sibling to the
  existing `providers_seen[]`, and each `assertions[]` entry gains a
  `"harness": str` field (DEC-010). Mirrors the #147 audit JSON
  v1 → v2 bump exactly.

`MAX_SCHEMA_VERSION` map post-#152:
`{"assertions.json": 2, "extraction.json": 4, "grading.json": 4}`.
`history.py` post-#152: `SCHEMA_VERSION = 3`,
`_ACCEPTED_SCHEMA_VERSIONS = frozenset({1, 2, 3})`.

`BlindReport` deliberately stays at `schema_version: 1` per DEC-014 —
`compare --blind` takes two pre-captured outputs as inputs, so there is
no skill execution at judge time and no harness axis to record. The
harness that produced each captured output is recorded in *that
capture's* sidecars, not on the blind-judge result.

**L1 placeholder inversion (DEC-008).** L1 audit rows now carry a real
`harness` value (sourced from `assertions.json` v2) but keep the
`provider: "anthropic"` placeholder from #147 — L1 makes no LLM call,
so provider is genuinely "no value." The two surfaces handle the
placeholder differently:

- **Stdout / Markdown renderers** show the L1 `provider` cell as `"—"`
  (em-dash) so the column structure stays uniform across L1/L2/L3 rows
  while signaling honestly that no provider attribution exists.
- **Audit JSON output (`render_json` v3)** retains the literal
  `"anthropic"` placeholder string so downstream JSON consumers see a
  fixed-shape value rather than a glyph that would force string-
  comparison branching.

The new pure helper
`src/clauditor/audit.py::_harness_or_default` — sibling of
`_provider_or_default` — handles defensive read of malformed v2/v4
sidecars (non-string truthy values, empty strings) and supplies the
`"claude-code"` default for v1/v2/v3 legacy reads with no `harness`
field. Both helpers are pure (no I/O, no logging) per
`.claude/rules/pure-compute-vs-io-split.md`.

`IterationRecord` and `AuditAggregate` both gained
`harness: str = "claude-code"` (DEC-006), mirroring the
`provider: str = "anthropic"` default introduced in #147. The `aggregate()`
grouping key widens from the 3-tuple `(provider, layer, id)` to the
4-tuple `(harness, provider, layer, id)` per DEC-007 — same `(provider,
layer, id)` under different harnesses now produces two distinct buckets
rather than averaging across harnesses. `apply_thresholds` and the
three renderers were updated in lockstep to consume the 4-tuple key.

The pytest fixtures `clauditor_grader` and `clauditor_triggers` auto-
populate `harness` from `EvalSpec.harness` resolution (the same
resolver path the CLI uses), threading it into `spec.run(
harness_name_override=...)` and onto the resulting `GradingReport`.
`clauditor_blind_compare` is unaffected (no harness axis at blind-judge
time per DEC-014).

Writers and readers (post-#152):

- `src/clauditor/runner.py::SkillResult.harness` — the foundational
  field; populated from `SkillRunner._invoke` reading
  `self.harness.name` (the `Harness.name` ClassVar per
  `.claude/rules/harness-protocol-shape.md`). Default `"claude-code"`
  keeps direct-construct test fixtures green.
- `src/clauditor/cli/grade.py::_write_assertions_sidecar` — accepts a
  `harness: str = "claude-code"` parameter; stamps
  `{"schema_version": 2, "harness": <name>, ...}` (canonical key
  order). The production call site in `_write_workspace_sidecars`
  threads the resolved harness name through.
- `src/clauditor/grader.py::ExtractionReport.to_json` /
  `ExtractionReport.from_json` — emits `schema_version: 4` with
  `"harness"` placed after `"provider_source"` to mirror the
  provider-sibling shape; reader defaults missing `harness` to
  `"claude-code"` for v1/v2/v3 reads.
- `src/clauditor/quality_grader.py::GradingReport.to_json` /
  `GradingReport.from_json` — same shape as `ExtractionReport`.
- `src/clauditor/audit.py::IterationRecord` /
  `src/clauditor/audit.py::AuditAggregate` — both carry the
  `harness: str = "claude-code"` field.
- `src/clauditor/audit.py::aggregate` — 4-tuple grouping key
  `(harness, provider, layer, id)`.
- `src/clauditor/audit.py::_records_from_assertions` /
  `_records_from_extraction` / `_records_from_grading` — all read
  through `_harness_or_default(data.get("harness"))` so legacy v1/v2/v3
  reads default cleanly.
- `src/clauditor/audit.py::_harness_or_default` — pure defensive-read
  helper, sibling of `_provider_or_default`.
- `src/clauditor/audit.py::render_stdout_table` /
  `render_markdown` / `render_json` — all surface the harness
  dimension (HARNESS column leftmost in stdout/markdown;
  `harnesses_seen[]` + per-entry `harness` in JSON v3).
- `src/clauditor/history.py::SCHEMA_VERSION` (= 3) /
  `_ACCEPTED_SCHEMA_VERSIONS` (= `frozenset({1, 2, 3})`) /
  `append_record` (mandatory keyword-only `harness=`; rejects blank
  and `"auto"`) / `read_records` (defaults missing `harness` to
  `"claude-code"` for v1/v2 reads).

### Schema version bumps for #170 (`reasoning_tokens` field)

`#170` added `reasoning_tokens: int | None` — the per-call
separately-billed reasoning / thinking-token count surfaced by
the grader's `ModelResult` chain — to the persisted token-totals
surface. The field rides alongside the existing `input_tokens` /
`output_tokens` totals on `GradingReport` / `ExtractionReport`,
sums across multi-call grader runs (variance, parse-retry,
blind-compare's two parallel calls), and threads from
`primary_report.reasoning_tokens` into the
`IterationContext.reasoning_tokens` placeholder that `#154`
pre-declared as nullable in v1.

Two schema bumps shipped together in #170 — accompanied by an
audit-loader range update and **two deliberate non-bumps** that
demonstrate the always-v1 pattern paying off:

- **`grading.json` v4 → v5** — adds nullable top-level
  `reasoning_tokens` field (placed after `output_tokens` in
  canonical key order). Per DEC-004 of
  `plans/super/170-reasoning-tokens-capture.md`.
- **`extraction.json` v4 → v5** — same shape (DEC-004).
- **audit `MAX_SCHEMA_VERSION` map widened** to accept the new
  v5 sidecars (not itself a `schema_version` bump — just the
  reader's accepted-range table tracking the writer-side bumps):
  `{"assertions.json": 2, "extraction.json": 5, "grading.json": 5,
  "context.json": 1}`.
- **`BlindReport` stays at `schema_version: 1`** per DEC-005.
  `BlindReport` has `to_json()` but no `from_json()` reader (no
  CLI command writes it as a sidecar today), so the additive
  nullable `reasoning_tokens` field has no legacy-read compat
  surface to maintain. This is the **always-v1-by-design**
  pattern (same as `context.json` itself) applied to a second
  artifact: when the only persistence path is `to_json()` and no
  reader exists, a nullable additive field is forward-compat-safe
  at the existing version.
- **`IterationContext` stays at `schema_version: 1`** by design.
  This is **the canonical example of the always-v1 contract
  paying off** that the `#154` subsection above predicted: the
  `reasoning_tokens: int \| None` placeholder pre-declared in v1
  is now populated by production writers, with zero schema bump
  and zero loader-side default-on-read churn. The on-disk shape
  is byte-identical between a pre-#170 `context.json` (with
  `"reasoning_tokens": null`) and a post-#170 `context.json`
  (with `"reasoning_tokens": 42`). Future tickets that wire up
  `cost_usd` (#169) will inherit the same property.

`audit.py::_records_from_*` helpers do NOT need to read the new
field — `reasoning_tokens` is a per-iteration concern (single
value per `IterationContext`), not a per-record concern. The
existing per-record loaders consume the v5 sidecars unchanged
because the field is additive at the top level only.

**Loader-side default-on-read.** `GradingReport.from_json` and
`ExtractionReport.from_json` default missing `reasoning_tokens`
to `None` for v1/v2/v3/v4 reads (mirrors the
`provider_source`-defaults-to-`"anthropic"` and
`harness`-defaults-to-`"claude-code"` patterns from #147 / #152).
Per DEC-007: the default `None` correctly represents "we don't
know whether reasoning happened" for pre-#170 history, which is
semantically distinct from `0` ("provider surfaced a count of
zero — model chose not to reason").

**`from_json` reader carries an explicit `bool` guard** per
`.claude/rules/constant-with-type-info.md` and symmetric with the
writer-side `_extract_reasoning_tokens` discipline (DEC-006). A
malformed sidecar carrying `"reasoning_tokens": true` would
otherwise silently coerce to `1` because Python's
`isinstance(True, int)` is `True`. The reader pattern is:

```python
raw_reasoning = parsed.get("reasoning_tokens")
reasoning_tokens: int | None
if raw_reasoning is None or isinstance(raw_reasoning, bool):
    reasoning_tokens = None
elif isinstance(raw_reasoning, int):
    reasoning_tokens = raw_reasoning
else:
    reasoning_tokens = None
```

This was added during #170's Quality Gate after a code-review
pass surfaced the asymmetry between writer-side (which has the
guard via `_extract_reasoning_tokens`) and reader-side (which
originally accepted any truthy int-like value). The guard now
fires symmetrically on both sides of the on-disk boundary.

**Writers and readers (post-#170):**

- `src/clauditor/quality_grader.py::GradingReport.to_json` /
  `GradingReport.from_json` — emits `schema_version: 5`; reader
  defaults missing `reasoning_tokens` to `None` and rejects
  `bool` values defensively.
- `src/clauditor/grader.py::ExtractionReport.to_json` /
  `ExtractionReport.from_json` — same shape.
- `src/clauditor/quality_grader.py::BlindReport.to_json` —
  schema_version stays `1`; emits `reasoning_tokens` field
  additively; no `from_json` reader to update.
- `src/clauditor/quality_grader.py::_sum_optional_reasoning_tokens`
  — pure helper computing the all-None→None / mixed→sum-of-non-
  None semantic across the multi-call grader chain.
- `src/clauditor/_providers/_openai.py::_extract_reasoning_tokens`
  — pure helper extracting the field from the OpenAI usage
  shape with the bool guard.
- `src/clauditor/_providers/_anthropic.py::_extract_result` —
  hardcodes `reasoning_tokens=None` per DEC-001 (no SDK field
  to read).
- `src/clauditor/cli/grade.py::_write_context_sidecar` — reads
  `primary_report.reasoning_tokens` (was hardcoded `None`
  pre-#170) and threads into `IterationContext.reasoning_tokens`.
  `cli/validate.py` keeps the `None` hardcode — `validate` has
  no LLM grader, so the value is structurally `None` (DEC-008).
- `src/clauditor/audit.py::MAX_SCHEMA_VERSION` — single-number
  edit per DEC-008 of #147 lifted both `extraction.json` and
  `grading.json` from `4` to `5`.

## When this rule applies

Any new persisted JSON file whose shape may evolve. Internal-only debug
dumps and transient files the codebase does not read back do not need a
version field.
