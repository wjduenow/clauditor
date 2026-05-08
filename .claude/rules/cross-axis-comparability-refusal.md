# Rule: Cross-axis comparability — refuse, filter, or opt in

When clauditor aggregates over a sequence of grading records (`trend`
averages a metric across history; `compare` deltas two grading
reports), and those records carry a **stack-identity dimension**
(today: `provider`, `harness`; tomorrow plausibly `transport_source`,
`grading_model`, `runner_version`) that is not numerically
commensurable across distinct values, the command MUST refuse the
mixed-dimension read by default and force the operator to choose
one of three responses **per axis**:

1. **Filter** — pass `--<dim> <value>` to narrow the set to one
   stack (only meaningful when the command consumes >2 records;
   `compare` ships only the opt-in side per DEC-007).
2. **Opt in** — pass `--cross-<dim>` to allow averaging / comparing
   across mixed values, with a single-line stderr `WARNING:` that
   the result may not be comparable.
3. **Fix the data** — re-run the missing stack so the history is
   uniform.

The principle is **surface, don't normalize**. A user who runs the
same eval under Claude+Sonnet one week and Codex+gpt-5.4 the next
deserves a refusal, not a smooth pass-rate line that silently
averages two non-comparable execution stacks. The refusal is the
mechanism; per-axis orthogonality is the contract.

## The pattern

### Layer 1 — pure detection helper (one seam, both commands)

A pure function in `audit.py` consumes already-loaded records and
returns the mixed-state for ONE axis. Both `trend` and `compare`
route through it so coercion semantics, sort order, and default-
fallback rules cannot drift between commands:

```python
# src/clauditor/audit.py — sibling of _provider_or_default /
# _harness_or_default. Pure, no I/O, never raises.
def detect_mixed_dimension(
    records: list[dict], *, dimension: Literal["harness", "provider"]
) -> tuple[bool, list[str]]:
    """Return ``(is_mixed, sorted_unique_values)`` for ``dimension``."""
    coercer = (
        _provider_or_default if dimension == "provider"
        else _harness_or_default
    )
    unique = sorted({coercer(rec.get(dimension)) for rec in records})
    return len(unique) > 1, unique
```

### Layer 2 — CLI: per-axis flags in two SEPARATE mutex groups

Each axis gets its own `add_mutually_exclusive_group()` containing
the `--<dim>` filter and the `--cross-<dim>` opt-in. **Two groups,
not one global mutex** — `--harness X --cross-provider` is valid
(filter the harness axis, allow mixed provider). A single global
mutex over-couples the axes.

```python
# src/clauditor/cli/trend.py
p_provider_group = p_trend.add_mutually_exclusive_group()
p_provider_group.add_argument("--provider", type=_provider_concrete_choice, ...)
p_provider_group.add_argument("--cross-provider", action="store_true", ...)

p_harness_group = p_trend.add_mutually_exclusive_group()
p_harness_group.add_argument("--harness", type=_harness_concrete_choice, ...)
p_harness_group.add_argument("--cross-harness", action="store_true", ...)
```

### Layer 3 — collect-then-print refusals (DEC-011 multi-axis)

Refusals from BOTH axes are collected into one list before any
print, so a history mixed on both axes produces both refusal
lines together (operator fixes the command in one round-trip).
Per-axis WARNINGs for opt-in flags are deferred until both axes
have cleared their refusal check, so the operator never sees a
WARNING above a refusal on a CI log:

```python
# src/clauditor/cli/trend.py::cmd_trend
provider_mixed, providers_seen = detect_mixed_dimension(records, dimension="provider")
harness_mixed,  harnesses_seen = detect_mixed_dimension(records, dimension="harness")

refusal_messages: list[str] = []

if args.provider is None and not args.cross_provider and provider_mixed:
    refusal_messages.append(
        f"ERROR: Mixed providers detected in history for skill "
        f"'{args.skill_name}' ({providers_str}). Pass "
        f"--provider anthropic (or --provider openai) to filter, "
        f"or --cross-provider to allow averaging."
    )

if args.harness is None and not args.cross_harness and harness_mixed:
    refusal_messages.append(
        f"ERROR: Mixed harnesses detected in history for skill "
        f"'{args.skill_name}' ({harnesses_str}). Pass "
        f"--harness claude-code (or --harness codex) to filter, "
        f"or --cross-harness to allow averaging."
    )

if refusal_messages:
    for msg in refusal_messages:
        print(msg, file=sys.stderr)
    return 2

# Then per-axis filter or WARNING-on-opt-in.
if args.provider is not None:
    records = [r for r in records if _normalized_provider(r) == args.provider]
elif args.cross_provider and provider_mixed:
    print(f"WARNING: averaging across providers ({providers_str}) — results may not be comparable.", file=sys.stderr)
# ... mirror for harness ...
```

### Layer 4 — verb shifts with the command (DEC-005)

The refusal/warning messages share a single template, but the verb
shifts to match the command's semantics:

- **`trend` averages**: `"WARNING: averaging across harnesses ..."`,
  `"... to allow averaging."`
- **`compare` compares two snapshots**: `"WARNING: comparing across
  harnesses ..."`, `"Pass --cross-harness to allow comparing."`

The verb is the only difference between the two commands' message
templates. Everything else (axis name, value list, single-line
shape, stable lead-in `"Mixed <plural>"`) is uniform across
commands and across axes.

## Why this shape

- **Detection runs BEFORE `--last` slicing.** Mixed-state must be
  computed from the full filtered set, not the sliced display
  window. Otherwise a user with mixed history could silently slip
  past the refusal by narrowing the window (`--last 5` on a history
  whose last 5 records happen to share a stack). The pure helper
  consumes the pre-slice list so the refusal can never be
  silently bypassed.
- **Stable lead-in substring, evolving suffix (DEC-008).** The
  refusal message keeps a byte-stable lead-in `"Mixed <plural>
  detected in history for skill"` so existing CI parsers and
  regression-test substring assertions keep matching across
  versions. Only the actionable suffix evolves (e.g. when the
  `--cross-<dim>` opt-in flag lands, the suffix gains an `or
  --cross-<dim>` clause). New tests assert on the new suffix
  substring; old tests on the lead-in still pass.
- **Two separate mutex groups, not one global (DEC-001 + API
  Design Review).** `--harness X --cross-provider` should be
  valid: filter one axis to a single value, allow mixed on the
  other. A single global mutex over `{--harness, --cross-harness,
  --provider, --cross-provider}` would reject this combination
  even though it is the most useful "two axes mixed but I only
  want to opt into one" call site. Per-axis groups model the
  operator's intent precisely.
- **Per-axis flags strictly orthogonal, no combined `--cross-axis`
  shortcut (DEC-002).** The shape teaches the user exactly which
  dimension they are opting into; the refusal message names the
  axis and proposes its specific opt-in flag. A combined flag
  hides which axis was being opted into and makes it harder to
  evolve when a third axis lands.
- **Multi-axis refusal names every still-uncovered axis at once
  (DEC-011).** When both axes are mixed and only one `--cross-*`
  is passed, the un-opted-in axis still refuses — and the
  refusal message instructs which additional flag to pass. The
  user fixes the command in one round-trip rather than iterating
  N times. This is enabled structurally by the collect-then-print
  list pattern above.
- **CLI-only opt-in flags, no spec field (DEC-002 +
  `.claude/rules/spec-cli-precedence.md`).** `--cross-harness`
  and `--cross-provider` are operator-intent toggles, not
  per-skill preferences. A skill author cannot know in advance
  whether THIS run, in THIS pipeline, at THIS moment, should
  tolerate cross-stack averaging. The flags ship as one-level
  CLI-only knobs (no `EvalSpec.cross_harness`, no precedence
  chain), matching the same pattern as `--no-api-key` /
  `env_override` from #64.
- **Pure helper sits with sibling axis utilities (DEC-010).**
  `detect_mixed_dimension` lives next to `_provider_or_default`
  and `_harness_or_default` in `audit.py` — the three are an
  axis-utility cluster. Promote to a `comparability.py` module
  only when a third utility lands (e.g. when #154/#155 add
  another axis-aware function, the cluster is large enough to
  earn its own file).
- **Silent-skip for `.txt` capture pairs (DEC-003).** When
  `compare` is given two raw `.txt` captures, the inputs carry
  no `harness` / `provider_source` metadata — there is nothing
  to compare. The check is gated on `before_kind == "grade.json"
  and after_kind == "grade.json"`; on `.txt` pairs the cross-axis
  block silent-skips. Manufacturing a warning ("we have no
  metadata to verify, so be careful!") would be hostile to the
  legacy capture workflow.
- **`compare --blind` is untouched.** Blind A/B compare is a
  per-call LLM judgment between two outputs, not a cross-history
  aggregate. The two outputs can legitimately come from different
  stacks — that is the entire point of the comparison. The
  cross-axis block is gated to delta mode only (positional
  `.grade.json` paths or numeric `--skill --from --to`); the
  `--blind` branch never enters it.
- **Per-invocation announcement, not per-process.** Each CLI
  invocation is a fresh process, so the
  `centralized-sdk-call.md` announcement-family pattern (module-
  level boolean flag + `_ANNOUNCEMENT` constant) is unnecessary
  here. The WARNING fires once per invocation by construction;
  no flag is needed.

## What NOT to do

- Do NOT fold both axes into a single global mutex group. A
  single mutex over the four flags rejects the legitimate
  `--harness X --cross-provider` combination (filter one axis,
  opt-in to mixed on the other). Two per-axis mutex groups model
  the contract precisely.
- Do NOT add a combined `--cross-axis` shortcut for "allow mixed
  on both axes." Operators should name the axis they are opting
  into. A combined shortcut hides which axis is being permitted
  and does not extend cleanly to a third axis.
- Do NOT add a spec field counterpart to `--cross-<dim>`. These
  are operator-intent toggles per `.claude/rules/spec-cli-
  precedence.md`. A skill author cannot make the cross-axis
  decision; only the operator running the command can.
- Do NOT normalize across dimensions. Adjusting scores via a
  cross-stack calibration table would re-introduce exactly the
  silent-averaging hazard the refusal exists to prevent. **Surface,
  don't normalize.**
- Do NOT drop the byte-stable `"Mixed <plural> detected"` lead-in
  for "cleaner" wording. Existing CI parsers and regression tests
  pin on this substring per DEC-008. New suffixes are additive;
  the lead-in is load-bearing.
- Do NOT print WARNINGs above refusals. Collect refusals from
  every axis first, print them together, and exit 2 — only emit
  WARNINGs after every axis has cleared its refusal check. A
  WARNING line directly above an ERROR line confuses both human
  operators and CI log parsers.
- Do NOT manufacture cross-axis warnings for `.txt` capture pairs.
  The capture format has no metadata; a warning here would be
  noise and would push users to "fix" inputs that are inherently
  metadata-less.
- Do NOT extend the cross-axis block to `compare --blind`. Blind
  A/B is a single LLM judgment over two outputs and is supposed
  to span stacks (that is the comparison). Gating the block to
  delta mode keeps blind compare's contract intact.
- Do NOT compute mixed-state AFTER `--last` slicing. The narrow
  window can hide the mismatch and silently bypass the refusal.
  The detection runs on the full filtered set before the slice.
- Do NOT inline the coercion helper. `_provider_or_default` and
  `_harness_or_default` are the canonical defenders against
  malformed records (non-string `provider`, `None`, blank
  whitespace); reusing them keeps the audit pipeline structurally
  string-typed.

## Canonical implementation

Pure detection helper:

- `src/clauditor/audit.py::detect_mixed_dimension` — `(records,
  *, dimension: Literal["harness", "provider"]) -> (bool,
  list[str])`. Sibling of `_provider_or_default` (audit.py:91)
  and `_harness_or_default` (audit.py:108). Both `trend` and
  `compare` route through this single seam.

CLI integration — `trend` (averages over many records):

- `src/clauditor/cli/trend.py::cmd_trend` — the
  `refusal_messages` collect-then-print block (~lines 195-227)
  + per-axis filter / WARNING branches (~lines 230-271). Two
  separate `add_mutually_exclusive_group()` calls in
  `add_parser` (~lines 94-134), one per axis.
- `src/clauditor/cli/trend.py::_normalized_provider` /
  `_normalized_harness` — per-record coercion helpers used by
  the filter branch (the aggregate `detect_mixed_dimension`
  helper's per-record shape doesn't fit per-record filtering).

CLI integration — `compare` (two-input delta mode):

- `src/clauditor/cli/compare.py::cmd_compare` — the cross-axis
  block (~lines 562-610) gated on
  `before_kind == "grade.json" and after_kind == "grade.json"`.
  Per-axis loop over `(("harness", cross_harness, "harness",
  "harnesses"), ("provider", cross_provider, "provider",
  "providers"))` collects refusals; opt-in axes emit WARNINGs
  inline. `compare --blind` is untouched (gated to delta mode).
- `src/clauditor/cli/compare.py::_load_grading_metadata` —
  reuses `GradingReport.from_json` to backfill canonical defaults
  for legacy v1/v2/v3 sidecars; raises `ValueError` for the
  caller's single `except ValueError` clause.
- `src/clauditor/cli/compare.py::add_parser` — `--cross-harness`
  and `--cross-provider` flags (~lines 91-114). NO `--harness`
  / `--provider` filter flags (DEC-007: compare has only two
  inputs; filtering doesn't fit the model).

Argparse type helpers:

- `src/clauditor/cli/__init__.py::_provider_concrete_choice`
  (~line 97) and `_harness_concrete_choice` (~line 115) —
  reject `"auto"` because `trend` reads pre-resolved history
  values. Sibling of `_provider_choice` / `_harness_choice`
  which DO accept `"auto"`.

Tests:

- `tests/test_audit.py::TestDetectMixedDimension` (line 2277)
  — six unit tests on the pure helper covering single-value /
  mixed / missing-key / non-string / harness mirror / empty
  input. No `tmp_path`, no subprocess mocks.
- `tests/test_cli.py::TestCmdTrend` (line 5178) — refusal +
  filter + opt-in + multi-axis tests for `cmd_trend` mirroring
  the existing `--provider` test set added in #147.
- `tests/test_cli.py::TestCmdCompareCrossAxis` (line 2807) —
  ten integration tests for `cmd_compare`'s cross-axis block,
  including the `.txt` silent-skip and `compare --blind`
  untouched-regression assertions.

Traces to DEC-001 through DEC-011 of
`plans/super/153-cross-axis-comparability.md`. Companion rules:
`.claude/rules/pure-compute-vs-io-split.md` (the pure-helper
shape `detect_mixed_dimension` follows),
`.claude/rules/spec-cli-precedence.md` (CLI-only opt-in
without spec-field counterpart, sibling pattern to
`env_override`), `.claude/rules/permissive-parser-strict-
validator.md` (refusal is the strict-validator layer running
AFTER the permissive load), `.claude/rules/llm-cli-exit-code-
taxonomy.md` (non-LLM 0/1/2 shape; refusal at exit 2),
`.claude/rules/pre-llm-contract-hard-validate.md` (whole-run
refusal; no partial output),
`.claude/rules/multi-provider-dispatch.md` (the `provider`
axis whose mixed-state this rule protects against silent
averaging).

## When this rule applies

Any future per-record stack-identity dimension that clauditor
groups history by but cannot numerically average across. The
next plausible candidate is **`transport_source`** (#86 — a
single skill might run under `cli` transport one day and `api`
transport the next; pass-rates are not safely comparable across
the two when retry semantics or error handling differ).
Plausible further callers:

- **`grading_model`** — averaging Sonnet-graded vs Haiku-graded
  pass-rates is silent miscomparison.
- **`runner_version`** — a runner version bump that changes
  exit-code behavior or transcript shape invalidates cross-
  version averaging until proven otherwise.
- **`eval_spec_hash`** — if the rubric or assertions change
  mid-history, the records before and after the change are not
  averageable.

For each new axis:

1. Ensure the axis has a `_<dim>_or_default` coercer in
   `audit.py` defending against malformed records.
2. Add a `Literal` member to `detect_mixed_dimension`'s
   `dimension` parameter and the dispatch table that picks the
   coercer.
3. Add `_normalized_<dim>` to `cli/trend.py` for per-record
   filtering (mirror `_normalized_provider`).
4. Add `_<dim>_concrete_choice` to `cli/__init__.py` if the
   axis has a closed value set; reuse the existing `_<dim>_choice`
   if the closed-set helper already exists and rejects `"auto"`.
5. Add a `--<dim>` filter + `--cross-<dim>` opt-in pair, each
   in its own `add_mutually_exclusive_group()` per axis on
   `trend`.
6. Add `--cross-<dim>` to `compare` (no filter side; DEC-007
   applies — compare has only two inputs).
7. Extend the collect-then-print refusal-list block in
   `cmd_trend` and `cmd_compare`.
8. Mirror the test classes (`TestCmdTrend` for trend,
   `TestCmdCompareCrossAxis` for compare) with single-axis +
   multi-axis cases.

## When this rule does NOT apply

- **`compare --blind`**. Blind A/B compare is a per-call LLM
  judgment between two outputs and is expected to span stacks.
  The cross-axis block is gated to delta mode (positional
  `.grade.json` paths or numeric `--skill --from --to`).
- **`audit`**. The audit command already groups by `(harness,
  provider, layer, id)` (#152) and the rendered output visually
  separates groups. No refusal is needed; the user-protection
  invariant is satisfied by visual separation.
- **`.txt` capture pairs in `compare`**. Raw text captures carry
  no harness/provider metadata; the cross-axis block silent-skips
  per DEC-003. Manufacturing a warning here would be noise.
- **Numerically commensurable per-record dimensions**. A
  dimension like `iteration_number` is monotonically ordered
  and aggregating across it is the entire point of `trend`. Do
  not invent a refusal for axes that have a well-defined
  averaging semantic.
- **Per-skill knobs that the skill author legitimately controls**.
  Anything that lives on `EvalSpec` and varies per-skill (model,
  rubric, prompt) is the author's choice; cross-skill comparison
  is already opt-in by virtue of the `--skill <name>` argument.
  This rule covers stack-identity dimensions that float
  independently of the skill, not skill-level config.
- **One-off diagnostic scripts in `scripts/`** that aggregate
  history ad-hoc. Operators running diagnostic scripts are
  expected to know what they are aggregating. The rule applies
  to first-class CLI commands users discover via `clauditor --help`.
