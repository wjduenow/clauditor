# Rule: Four-exit-code taxonomy for LLM-powered CLI commands

Every clauditor CLI command that wraps a single Anthropic call uses
the same four-exit-code taxonomy, routing distinct failure categories
to distinct codes so downstream scripts, CI pipelines, and consuming
tools can branch cleanly:

- **0 — success.** Artifact written (or printed to stdout via
  `--json`, `--dry-run`, etc.).
- **1 — load-time / parse-layer failure.** Missing prior sidecar the
  command reads from, existing output without `--force`, model
  returned unparseable JSON, OS/disk error writing the final artifact.
  Roughly "the request was well-formed but the surrounding state is
  not ready / not coherent."
- **2 — input-validation failure.** Pre-call input errors (oversize
  token budget, missing required skill file, malformed spec layout,
  `--from-capture` / `--from-iteration` pointing at a missing target)
  AND post-call invariant failures (LLM output structurally valid
  JSON but violates a domain invariant — anchor not found, proposed
  spec fails `EvalSpec.from_dict`). Roughly "the LLM call either
  shouldn't happen or its output cannot be trusted."
- **3 — Anthropic API failure.** `AnthropicHelperError` surfacing an
  auth error, rate-limit exhaustion, 5xx, or connection failure.
  Roughly "something outside our control went wrong; retry later."

Do NOT invent a fifth category. Do NOT collapse categories 2 and 3
into one "bad exit"; pipelines need the split to decide retry vs
don't-retry.

## The pattern

Each CLI command plumbs errors through the async orchestrator's
report dataclass, which carries **distinct fields per failure
category** — NOT a single `error: str` field that the CLI
substring-matches to pick an exit code:

```python
@dataclass
class ProposeEvalReport:
    api_error: str | None = None          # routes to exit 3
    validation_errors: list[str] = ...    # routes to exit 1 OR 2
    # (parse-layer failures use a stable "parse_<name>:" prefix
    #  inside validation_errors so the CLI can route them to 1
    #  without a brittle substring search.)
```

The CLI dispatcher is a linear chain of early-return branches,
ordered from "most external" (API) down to "most internal"
(success), with pre-call input errors guarded **before** any
Anthropic call is made:

```python
async def _cmd_propose_eval_impl(args) -> int:
    # Pre-call input errors → exit 2 (before any API spend).
    if not skill_md_path.is_file():
        print(f"ERROR: skill file not found: {skill_md_path}")
        return 2

    try:
        prompt = build_propose_eval_prompt(propose_input)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(prompt)
        return 0  # no Anthropic call for preview

    report = await propose_eval(propose_input, ...)

    # Anthropic API failure → exit 3.
    if report.api_error is not None:
        print(f"ERROR: {report.api_error}", file=sys.stderr)
        return 3

    # Parse-layer failure → exit 1. Tagged by a stable prefix on
    # the error string, NOT a substring search on error content.
    if report.validation_errors and any(
        err.startswith("parse_propose_eval_response:")
        for err in report.validation_errors
    ):
        for msg in report.validation_errors:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    # Post-call invariant failure → exit 2.
    if report.validation_errors:
        for msg in report.validation_errors:
            print(f"  - {msg}", file=sys.stderr)
        return 2

    target.write_text(json.dumps(report.proposed_spec, indent=2))
    return 0
```

## Why this shape

- **CI pipelines need retry vs don't-retry split.** Exit 3 is the
  only category a pipeline should retry on (rate limits, transient
  5xx). Exit 2 means the user's input is structurally bad —
  retrying the same input burns more API quota for the same
  failure. A single "bad exit" code collapses both into an
  uncertain retry decision.
- **Exit 1 vs 2 distinguishes "fix the environment" from "fix the
  input".** Exit 1 fires for "eval.json already exists without
  `--force`" or "no prior grading.json" — the user adjusts a flag
  or runs a prerequisite command. Exit 2 fires for "anchor not
  found" or "proposed spec fails validation" — the LLM output is
  wrong; regenerating with the same inputs might succeed, but the
  spec itself isn't broken. A single exit code conflates these.
- **Pre-call errors route to exit 2, not exit 3.** An oversize
  token budget is a pre-call input check: it would spend an API
  call if not guarded. Routing it to exit 2 groups it with "user's
  input is bad"; routing it to exit 3 would falsely imply an API
  round-trip happened. DEC-006 in
  `plans/super/52-propose-eval.md` and DEC-008 in
  `plans/super/27-suggest-proposer.md` both call out this choice.
- **Distinct report fields avoid brittle substring routing.** An
  earlier iteration tried to multiplex every failure into a single
  `error: str` field; the CLI then used substring matches to
  recover the category. This works until a new error message
  phrases the substring differently, at which point the pipeline
  silently routes to the wrong category. Separate fields +
  prefix-tagging (`"parse_propose_eval_response:"`) makes the
  classification structural.
- **Stable stderr format per category.** Categories 1 and 3 print
  a single `ERROR: <message>` line; category 2 prints a header
  line + one `  - <message>` line per error. CI parsers key on
  the shape.

## Canonical implementation

Two commands share this taxonomy verbatim:

- `src/clauditor/cli/suggest.py::_cmd_suggest_impl` — DEC-008 in
  `plans/super/27-suggest-proposer.md`. Uses `SuggestReport` with
  `api_error`, `parse_error`, `validation_errors` (anchor
  failures). Per US-003 of #162 also loads
  `SkillSpec.from_file(args.skill)` at the seam, resolves
  `provider = skill_spec.eval_spec.grading_provider or
  "anthropic"`, and dispatches `check_provider_auth(provider,
  "suggest")` with distinct `AnthropicAuthMissingError` and
  `OpenAIAuthMissingError` except branches — both routing to
  exit 2, structurally separate from `*HelperError` (exit 3).
- `src/clauditor/cli/propose_eval.py::_cmd_propose_eval_impl` —
  DEC-006 in `plans/super/52-propose-eval.md`. Uses
  `ProposeEvalReport` with `api_error` and `validation_errors`;
  parse failures use a `"parse_propose_eval_response:"` prefix
  inside `validation_errors` for structural routing.

Both orchestrators (`propose_edits`, `propose_eval`) follow the
companion contract: **never raise**. Every failure category lands
in a distinct report field, and the CLI is the single place that
maps those fields to exit codes. The async layer is exception-free
by construction, so no "uncaught exception → exit 1" path can
sneak in and collapse category 3 into category 1.

Traces to: DEC-008 (`plans/super/27-suggest-proposer.md`),
DEC-006 (`plans/super/52-propose-eval.md`), and bead
`clauditor-2ri` epic #52.

## When this rule applies

Any new CLI command that:

1. Wraps a single Anthropic call via
   `clauditor._anthropic.call_anthropic` (see
   `.claude/rules/centralized-sdk-call.md`), AND
2. Produces a persisted sidecar or structured stdout on success,
   AND
3. Has at least one "validate LLM output against an invariant"
   branch (see `.claude/rules/pre-llm-contract-hard-validate.md`).

Candidate future commands: a rubric critic (`clauditor critique`),
a trigger proposer (`clauditor propose-triggers`), a regeneration-
loop runner. Each should carry the same four-exit-code table and
the same distinct-fields report shape.

## When this rule does NOT apply

- Non-LLM commands. `clauditor audit`, `clauditor trend`,
  `clauditor setup` do not spend API calls and do not need the
  exit-3 category; their exit codes can follow a simpler 0/1/2
  table.
- Commands that fan out to many API calls (e.g. variance reps).
  The per-call failure aggregation is richer — each call's
  category contributes to an overall report. The single-call
  exit-code taxonomy does not directly apply; use it as a per-rep
  classifier if useful.
- Interactive commands that loop on user input. A TUI may use
  exit codes differently (e.g. 0 on clean quit, 130 on SIGINT).
  This rule is about batch/CI-style one-shot commands.
