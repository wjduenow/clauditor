# Rule: Numeric judge fields define 0.0/1.0 anchors and alignment with boolean siblings

When an LLM judge prompt asks the model to emit a **numeric** field
in a per-criterion result object — `score`, `confidence`, `severity`,
a future `quality` rating — the prompt MUST (a) define what the
endpoints of the numeric range mean concretely, and (b) if the numeric
field has a boolean sibling in the same object (`passed`, `valid`,
`triggered`), declare the monotonic-alignment invariant tying them
together at a specific threshold. A bare `"score: 0.0 to 1.0"` or
`"confidence level from 0.0 to 1.0"` is the foot-gun this rule
exists to prevent: Anthropic Claude reads such phrasing as graded
fulfillment ("how passing is this"), gpt-5.4 reads it literally as
verdict confidence ("how sure am I about my passed value"), and the
two readings produce non-commensurable values for the same on-disk
field within a single grader run.

The cross-provider divergence is **silent** — both responses parse
cleanly, both look plausible field-by-field, and the divergence only
surfaces when an aggregator averages across the two (mean_score
divergence) or a downstream consumer reads the numeric field as an
ordering signal.

## The trap

```python
# WRONG — "confidence" is provider-ambiguous; no threshold tying
# score to passed.
return (
    f"For each criterion, determine:\n"
    f"- passed: whether the output satisfies the criterion (true/false)\n"
    f"- score: confidence level from 0.0 to 1.0\n"
    f"- evidence: ...\n"
    f"- reasoning: ...\n"
)
```

Observed failure shape on the same captured output, same eval spec
(`find-restaurants` fixture, 13 criteria):

| Provider                | Pass rate | Mean score | `passed=false, score>=0.5` rows |
| ----------------------- | --------- | ---------- | ------------------------------- |
| claude-sonnet-4-6       | 10/13     | 0.785      | 0/13 (failing scores 0.0–0.4)   |
| gpt-5.4 (pre-rule)      | 8/13      | **0.965**  | **5/13** (failing scores 0.93–1.0) |

gpt-5.4 emitted `passed=false, score=1.0` rows, treating `score` as
"how confident am I in this failure verdict" — orthogonal to
`passed`. `mean_score` aggregation across the two providers became
meaningless: gpt-5.4 reports 0.965 while passing fewer criteria,
which a naive trend consumer would read as "better quality".

## The pattern

```python
# RIGHT — anchored endpoints + monotonic-alignment invariant.
return (
    f"For each criterion, determine:\n"
    f"- passed: whether the output satisfies the criterion (true/false)\n"
    f'- score: fulfillment level from 0.0 to 1.0, where 0.0 means'
    f' "the output completely fails this criterion" and 1.0 means'
    f' "the output completely satisfies this criterion". This MUST'
    f" be monotonically aligned with `passed`: any score >= 0.5"
    f' implies "passed": true, and any score < 0.5 implies'
    f' "passed": false.\n'
    f"- evidence: ...\n"
    f"- reasoning: ...\n"
)
```

Three load-bearing clauses, all required:

1. **A semantic name for what the numeric field measures.**
   `"fulfillment level"` (or `"severity"`, `"completeness"`,
   `"alignment"`) — NOT the word `"confidence"`, which is the
   verdict-confidence trap. Pick a noun that describes the
   property being measured, not the model's certainty about its
   answer.
2. **Concrete sentences anchoring the endpoints.** What does 0.0
   *mean*? What does 1.0 *mean*? `"completely fails this
   criterion"` / `"completely satisfies this criterion"` are
   unambiguous; `"low confidence"` / `"high confidence"` are not.
3. **The monotonic-alignment invariant** at an explicit threshold,
   in MUST language, naming both directions. `"any score >= 0.5
   implies passed=true, and any score < 0.5 implies passed=false"`
   — both implications, the threshold pinned. This is the clause
   that tells the model "do not decouple the numeric field from
   the boolean field, regardless of how you interpret the
   numeric scale."

## Why each piece matters

- **Word choice (`"confidence"` is the trap)**: the word
  `"confidence"` has a separate technical meaning in ML / decision
  theory that gpt-5.4 honors literally (verdict confidence,
  orthogonal to verdict correctness). Anthropic Claude has been
  observed treating `"confidence"` more idiomatically in this
  context — closer to "graded fulfillment" — but the prompt
  cannot rely on idiomatic reading. The remedy is to NOT use
  `"confidence"` for the fulfillment-axis field at all. If
  verdict-confidence is genuinely the property you want to
  measure, name a SECOND field `confidence` and keep `score`
  separate.
- **Anchored endpoints, not range alone**: `"0.0 to 1.0"` says
  nothing about what the endpoints mean. A model facing an
  ambiguous range will fall back to whatever its training
  distribution suggests, which differs across providers. Spelling
  out `"0.0 means X, 1.0 means Y"` removes the ambiguity at the
  prompt boundary, where it costs nothing, instead of trying to
  recover it at the parser boundary, where there is no signal to
  recover from.
- **MUST + threshold + both directions**: the monotonic-alignment
  clause is the load-bearing piece. Without it, even an anchored
  range can produce decoupled values (a model could rate
  `passed=false` and `score=0.6` claiming "this output mostly
  fulfills the criterion but tips below the bar"). The 0.5
  threshold matches the natural pass/fail boundary the aggregator
  already implicitly assumes (`mean_score >= 0.5` ↔ "passing
  quality"). Naming both implications (`>=0.5 → true`, `<0.5 →
  false`) prevents a one-sided reading.
- **Prompt-side fix, not parser-side reconciliation**: tempting
  alternative is to have the parser detect violation and rewrite
  `score` to match `passed` (e.g. `if not passed and score >= 0.5:
  score = 1 - score`). DO NOT do this. The model's score is its
  judgment; rewriting it after the fact loses information about
  how the model actually graded. The right answer is to fix the
  prompt so the model emits commensurable values in the first
  place. The parser stays strict on shape: validates types,
  alignment with criteria, but never rewrites field values.
- **Cross-provider validation is the only real test**: unit tests
  that pin substrings catch regression of the prompt language,
  but the only way to verify the language actually produces
  commensurable values is to grade the same captured output
  against both providers and compare. A `tests/` test cannot do
  this without spending real tokens; the validation belongs in
  PR-review-time manual or smoke-test territory, not CI.

## What NOT to do

- Do NOT use the word `"confidence"` for a fulfillment-axis
  numeric field. It is the provider-ambiguity trap this rule
  exists to prevent. If the field genuinely measures
  verdict-confidence, use a different field name and ALSO declare
  its anchors and (if applicable) its relationship to the
  fulfillment field.
- Do NOT use range-only definitions (`"score: 0.0 to 1.0"`).
  Anchor the endpoints with concrete sentences.
- Do NOT omit the monotonic-alignment clause when the numeric
  field has a boolean sibling. The two will silently decouple on
  some provider, some prompt, some run.
- Do NOT enforce monotonic alignment in the parser by rewriting
  the numeric field. Rewriting loses signal. Fix at the prompt.
- Do NOT skip the cross-provider validation step on a real
  captured fixture before merging a new numeric judge prompt.
  Unit tests pin the prompt language but cannot verify the
  language produces commensurable values across providers.
- Do NOT pick a non-0.5 threshold without a reason. The 0.5
  midpoint is the natural pass/fail boundary the aggregator
  already implicitly uses (`mean_score >= 0.5` reads as "passing
  quality"). A different threshold would create a second source
  of truth for "what counts as passing".

## Canonical implementation

`src/clauditor/quality_grader.py::build_grading_prompt` (post-#186)
— the `score` clause defines fulfillment level with concrete
endpoints and the MUST monotonic-alignment invariant tying it to
`passed` at 0.5. The pre-#186 phrase `"confidence level from 0.0
to 1.0"` is the documented foot-gun.

Tests:

- `tests/test_quality_grader.py::TestBuildGradingPrompt::test_score_defined_as_fulfillment_not_confidence`
  — pins `"fulfillment level from 0.0 to 1.0"` plus the
  completeness clauses; asserts the pre-#186 `"confidence level
  from 0.0 to 1.0"` phrase is gone.
- `tests/test_quality_grader.py::TestBuildGradingPrompt::test_score_monotonically_aligned_with_passed`
  — pins the MUST clause and both threshold implications.
- `tests/test_quality_grader.py::TestBuildGradingPrompt::test_prompt_language_would_have_prevented_gpt5_shape`
  — documents the two response shapes (Claude-style fulfillment
  vs gpt-5.4 verdict-confidence) and pins the load-bearing
  clauses that rule out the gpt-5.4 shape.

Cross-provider validation evidence: PR #187 comment on the
`find-restaurants` fixture, 13 criteria, grading the same
captured output. Pre-fix gpt-5.4 showed 5/13 monotonic-alignment
violations; post-fix gpt-5.4 showed 0/13, with failing-criterion
scores moving from 0.93–1.0 down to 0.0–0.4.

## Companion rules

- `.claude/rules/positional-id-zip-validation.md` — sibling rule
  for the same class of cross-provider prompt divergence on a
  different shape (textual `criterion` echo field). The
  "Prompt-side companion" section there documents the same
  Anthropic-vs-gpt-5.4 divergence pattern for `1. ` prefix
  echoing. Both rules trace to real-world validation against the
  same `find-restaurants` fixture, both fix prompt-side, both
  pin substring assertions to catch prompt regressions.
- `.claude/rules/llm-judge-prompt-injection.md` — the prompt
  hardening rule for untrusted content. This rule extends the
  prompt-builder discipline from "treat tagged content as data"
  to "anchor numeric fields to concrete semantics".
- `.claude/rules/pre-llm-contract-hard-validate.md` — the broader
  "fail loudly at the earliest safe moment" principle. This rule
  applies that principle at the prompt boundary: the cheapest
  place to prevent non-commensurable judge output is in the
  prompt-builder, not in the parser or aggregator.
- `.claude/rules/cross-axis-comparability-refusal.md` — the
  downstream refusal mechanism that would catch averaging
  across non-commensurable history records. This rule prevents
  the non-commensurability from arising in the first place,
  within a single run.

## When this rule applies

Any future LLM judge prompt that asks the model to emit a numeric
field per result. Plausible callers:

- A rubric critic that emits a `severity` field on each criticism.
- A trigger-precision judge with a `confidence` field tied to a
  `should_trigger` boolean.
- A blind-compare judge with a `margin` field measuring how
  decisively one output won.
- A regression-detection judge with a `quality_delta` field
  tied to a `regressed` boolean.

The rule also applies retroactively: any existing judge prompt with
a bare `"0.0 to 1.0"` definition is a latent foot-gun. Add anchored
endpoints + monotonic-alignment (if applicable) the next time the
prompt is touched. Run the cross-provider validation step before
merging.

## When this rule does NOT apply

- Non-numeric judge fields. Categorical fields (`"verdict": "a" |
  "b" | "tie"`), enumerated fields, free-text fields — those have
  their own discipline. The verdict-vs-fulfillment ambiguity is
  specific to ranged numeric fields.
- Single-provider prompts that genuinely never run against more
  than one model. Rare in clauditor today (most LLM-mediated
  CLIs accept `--grading-provider`), but possible for a future
  internal-only judge.
- Numeric fields with no boolean sibling. The anchored-endpoints
  half of the rule still applies; the monotonic-alignment half
  does not.
- Diagnostic / debug prompts that print a free-form analysis
  rather than a structured per-criterion result object. There is
  no parser-side commensurability invariant to defend.
