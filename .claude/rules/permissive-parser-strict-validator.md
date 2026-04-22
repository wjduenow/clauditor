# Rule: Permissive parser at the parse boundary, strict validator at the conformance layer

When a module accepts user-authored text that will later be rendered
by external tools (GitHub, CI validators, schema checkers, other
parsers), split the load path into **two** layers:

- **A permissive parser** that accepts the tolerated on-disk shape
  and extracts a data structure. Its job is "get the data out of
  the file without losing information". It returns a dict (or
  dataclass) on success and raises on unrecoverable corruption.
- **A strict validator** that runs AFTER the parser succeeds,
  inspects the parsed dict AND the raw text where needed, and
  emits diagnostics for every way the input would fail a stricter
  downstream consumer. Its job is "prevent the user from shipping
  a file that renders fine locally but breaks elsewhere."

Do NOT fold strict correctness checks into the permissive parser.
The two layers have different contracts: parsing is "can we read
it?", validating is "should it exist in this shape?" — conflating
them makes error messages worse and blocks legitimate partial-file
workflows (e.g. diagnostic scripts, incremental edits, historical
reads).

## The pattern

### Parser — permissive, minimal, single job

```python
# _frontmatter.py
def parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """Read SKILL.md-style frontmatter; return (data, body).

    Accepts the shape real SKILL.md files use today, not full YAML.
    No strict-YAML-spec compliance: we tolerate unquoted scalars
    with embedded punctuation, comma-separated strings in list
    fields, etc. Downstream conformance checks catch shapes that
    strict consumers would reject.
    """
    # ... 80 lines of dict-building ...
```

### Validator — strict, diagnostic-emitting, runs on parsed + raw

```python
# conformance.py
def check_conformance(
    skill_md_text: str, skill_path: Path
) -> list[ConformanceIssue]:
    """Run every agentskills.io spec rule against a SKILL.md.

    Runs the permissive parser first; if it raises, we surface an
    ``AGENTSKILLS_FRONTMATTER_INVALID_YAML`` issue and short-circuit.
    Otherwise we run a chain of ``_check_*`` functions over the
    parsed dict (and, for a handful of raw-text-only checks, the
    original ``skill_md_text``).

    Every check appends to the issue list in place. Never raises.
    Returns the concatenated issue list.
    """
    issues: list[ConformanceIssue] = []
    try:
        parsed, body = parse_frontmatter(skill_md_text)
    except ValueError as exc:
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_FRONTMATTER_INVALID_YAML",
                severity="error",
                message=f"Frontmatter YAML is malformed: {exc}",
            )
        )
        return issues

    # Raw-text-only checks run before the parsed-dict chain — they
    # catch YAML ambiguities that the permissive parser happily
    # accepts (e.g. unquoted space-colon-space inside scalars).
    _check_unquoted_colon_in_scalar(skill_md_text, issues)

    # Parsed-dict checks for every spec rule.
    _check_name(parsed, skill_path, issues)
    _check_description(parsed, issues)
    _check_compatibility(parsed, issues)
    # ... etc ...

    return issues
```

## Why this shape

- **Different contracts, different concerns.** The parser's
  contract is "preserve every byte of user-authored information
  into a Python object"; the validator's contract is "report every
  way the input would fail a strict downstream consumer". A single
  combined function would have to satisfy both contracts at once,
  producing a confused error model where a failed strict check
  looks indistinguishable from a failed parse.

- **Permissive parsing enables diagnostic tooling.** Scripts that
  want to peek at frontmatter for debugging, incremental editors
  that want to preview partial content, and rescue tools that
  salvage data from malformed files all benefit from a parser
  that tolerates gray-area shapes. A strict parser raises on
  anything ambiguous; a permissive one returns best-effort data
  and lets the caller decide what to do. The clauditor use case:
  `clauditor lint` runs the strict validator; other clauditor
  commands that just want the frontmatter for non-validation
  purposes run only the permissive parser.

- **Strict validation is the right place for "what renders
  downstream" checks.** The conformance layer knows about external
  contracts (agentskills.io spec, GitHub's YAML renderer, Claude
  Code's slash-command discovery) that are outside the parser's
  scope. Piling them into the parser would bloat a module whose
  whole point is minimalism. Keeping them in the validator lets
  the parser stay small and the validator grow as external
  contracts evolve.

- **Error messages improve when concerns are split.** The parser's
  errors describe structural corruption ("missing closing
  delimiter"); the validator's errors describe semantic violations
  ("line 4 has ': ' inside an unquoted value — strict parsers
  reject this"). Each message can be precise because the layer
  knows its own invariants. A combined layer would have to emit
  messages that straddle both.

- **The "permissive + strict" split scales to multiple strict
  layers.** Today clauditor has one validator (`conformance.py`).
  A future project might have two: one for the agentskills.io
  spec, one for a registry-specific shape. They can both consume
  the same permissive parser output without fighting over parse
  behavior.

- **Failure mode: strict checks in the permissive parser.** If
  the parser starts raising on shapes the conformance layer
  already flags, the failure mode downgrades from "surface every
  issue at once" to "surface the first issue and stop". Users
  fixing a file with multiple bugs have to iterate N times, one
  bug per round-trip, instead of seeing all N at once.

## What NOT to do

- Do NOT add "strict YAML" checks to the permissive parser. The
  parser's contract is minimalism; strictness belongs in the
  validator.
- Do NOT make the validator raise on issues — it must return a
  list of `ConformanceIssue` so the caller can decide how to
  surface them (stderr, structured JSON, etc.). See
  `.claude/rules/pure-compute-vs-io-split.md`.
- Do NOT duplicate validation logic across the two layers. If the
  parser already rejects a shape (e.g. missing closing `---`),
  the validator doesn't need a redundant check — let the
  parser's `ValueError` flow through the validator's
  `AGENTSKILLS_FRONTMATTER_INVALID_YAML` branch.
- Do NOT make the validator raise to "promote" an issue. Return
  it as an error-severity `ConformanceIssue` and let the CLI /
  caller route to the right exit code.
- Do NOT inline strict checks into permissive-parse code paths
  just because they share a line-by-line walk. The walk is
  cheap; two walks are still O(n) and the separation is worth
  the tiny duplication.

## Canonical implementation

- Parser: `src/clauditor/_frontmatter.py::parse_frontmatter`. Explicit
  minimalism: the module docstring enumerates the shapes it
  supports. Raises `ValueError` on structural corruption; tolerates
  ambiguous-but-readable shapes (unquoted scalars with embedded
  punctuation, comma-separated list fields, etc.).
- Validator: `src/clauditor/conformance.py::check_conformance`. 24+
  `AGENTSKILLS_*` codes enforce the agentskills.io spec + Claude-
  Code-specific invariants (parent-dir-name match, frontmatter
  shape). Runs the parser first, short-circuits on `ValueError`,
  then runs the `_check_*` chain over the parsed dict AND the raw
  text for checks that need byte-level visibility (e.g.
  `AGENTSKILLS_FRONTMATTER_UNQUOTED_COLON_IN_SCALAR` walks the
  raw text line-by-line because quote-awareness requires pre-
  parse information).

The #80 refactor is the latest reinforcement of this pattern: when
a SKILL.md with unquoted `space-colon-space` in a scalar value
shipped (GitHub's strict renderer rejected it but clauditor's
parser didn't), the fix was a new `_check_*()` in the validator
layer — NOT a new rejection in the parser. The parser stays
permissive; the validator grows to match new external contracts.

Traces to: DEC-001, DEC-002, DEC-004 of
`plans/super/80-strict-frontmatter-yaml.md`.

## Companion rules

- `.claude/rules/pure-compute-vs-io-split.md` — the validator's
  pure-compute contract (no I/O, never raises, returns a list).
  The two rules compose: a validator that satisfies both is pure
  and strict.
- `.claude/rules/pre-llm-contract-hard-validate.md` — the general
  "fail loudly at parse boundaries" shape, applied here at the
  validator boundary (not the parser).
- `.claude/rules/constant-with-type-info.md` — for validators
  whose diagnostic codes need a central table. clauditor's
  `conformance.py` declares codes inline rather than in a table
  (convention established by the first 24 codes); new codes
  should match that convention.

## When this rule applies

Any future work where:

- A module accepts user-authored text or config (SKILL.md,
  `eval.json`, a hand-written rubric, a regen-proposer output)
  AND
- That text will later be consumed by a stricter external
  contract (a renderer, a registry, a CI validator) where errors
  cost the user real time
  AND
- The permissive parse is useful on its own for diagnostic or
  incremental workflows.

Examples of future callers:

- A rubric-criteria validator that runs stricter checks than the
  spec loader.
- A plugin-upload validator that enforces registry-specific
  shapes beyond what `SkillSpec.from_file` requires.
- A `clauditor propose-eval` regen pipeline that validates the
  LLM's output against both the loader's contract AND the
  downstream clauditor-pipeline contract.

## When this rule does NOT apply

- Internal-only data formats with no downstream consumers (a
  debug dump, a transient cache). The strict validator layer has
  no audience.
- Loaders that genuinely cannot read the input without applying
  the strict rule (e.g. a binary format where byte-level
  structure is the validity contract). Separation is impossible.
- Cases where the parser already IS the validator by design (e.g.
  a protobuf-generated decoder). The split has no room to land.
- One-off scripts with a single caller and no workflow implications.
  The ceremony of two layers costs more than it saves.
