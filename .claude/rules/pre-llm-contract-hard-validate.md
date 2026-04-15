# Rule: Pre-LLM contract + post-LLM hard validate

When an LLM is asked to produce structured output that must satisfy a
specific invariant (e.g., "every anchor appears exactly once in the
source text", "every referenced id exists in the input", "every edit
is applicable to SKILL.md"), **assert the invariant in the prompt
*and* enforce it in the parser**. Never trust the prompt assertion
alone to hold; never silently accept output that violates it. If any
item fails validation, fail the whole run — do not publish a partial
artifact.

## The pattern

**Step 1 — write the invariant into the prompt in a dedicated,
load-bearing block.** Not a footnote, not a "please try to", not a
JSON schema description field. A short, imperative sentence the model
cannot miss:

```python
def build_suggest_prompt(input: SuggestInput) -> str:
    return f"""...task description...

    {skill_md_text}

    ### ANCHOR CONTRACT
    Each `anchor` MUST be a verbatim substring of the SKILL.md text
    shown above, appearing **exactly once** in that text. If you
    cannot locate a suitable unique anchor for an edit, omit that
    edit.

    ...response schema...
    """
```

The phrase "exactly once" is what the validator will enforce, and it
should appear in the prompt verbatim so a grep on the prompt-builder
tests can anchor on it.

**Step 2 — hard-validate in the parser.** Do not "fuzzy match" or
"fix up" bad output. If the invariant fails, record a specific,
debuggable error (the edit id, the signal ids that motivated it, the
observed count) and do not include the edit in the returned artifact.

```python
def validate_anchors(proposals, skill_md_text) -> list[str]:
    errors = []
    proposed = skill_md_text  # simulate sequential apply
    for p in proposals:
        count = proposed.count(p.anchor)
        if count == 0:
            errors.append(
                f"{p.id} (motivated_by={p.motivated_by}): "
                "anchor not found in SKILL.md"
            )
            continue
        if count > 1:
            errors.append(
                f"{p.id} (motivated_by={p.motivated_by}): "
                f"anchor appears {count} times in SKILL.md (must be "
                "exactly once)"
            )
            continue
        proposed = proposed.replace(p.anchor, p.replacement, 1)
    return errors
```

**Step 3 — route any non-empty error list to a whole-run failure at
the CLI layer.** Do not publish a sidecar. Do not render a partial
diff. Exit non-zero with the error list on stderr so the user can re-
run or adjust. Partial artifacts are worse than no artifact: they
look correct at a glance and land in audit history as if the model
had done something useful.

## Why this shape

- **Prompt assertions get ignored under load.** Models occasionally
  violate "musts", especially when the request is long or the source
  text is ambiguous. The validator is the only real guarantee.
- **Fuzzy matching reintroduces drift.** "The model returned a close
  anchor, let me realign it" is how stable-id work gets undone at
  2am. Either the anchor is correct or the edit is rejected.
- **Partial artifacts corrupt history.** A sidecar with 3 good edits
  and 1 silently-dropped bad edit is indistinguishable from a sidecar
  where the model never proposed the 4th edit. The whole-run failure
  forces an explicit "try again" loop.
- **Sequential simulation catches later-edit collisions.** When edits
  apply to a mutating buffer (like `str.replace`), an edit's anchor
  must exist *after* earlier edits have applied. A check against the
  *original* text misses the case where edit[0]'s replacement
  destroys or duplicates edit[1]'s anchor. The validator must walk
  the proposals in declaration order, updating its view of the text
  with each accepted edit.

## Canonical implementation

`src/clauditor/suggest.py` — the `clauditor suggest` command.

- **Prompt:** `build_suggest_prompt` places the anchor contract in a
  dedicated block with the literal phrase "exactly once" so tests can
  anchor on it.
- **Parser:** `parse_suggest_response` hard-rejects `motivated_by`
  ids that are not present in the `SuggestInput` (the positional-id
  variant of the same pattern — see also
  `.claude/rules/positional-id-zip-validation.md`).
- **Validator:** `validate_anchors` sequentially simulates the apply
  used by `render_unified_diff` and reports per-edit failures.
- **CLI router:** `_cmd_suggest_impl` in `cli.py` maps any non-empty
  `validation_errors` list to exit code 2 with a stderr report and
  writes no sidecar.

## When this rule applies

Any future LLM-producing-structured-edits feature:

- A rubric critic that must reference rubric criteria by id.
- A patch synthesizer that must produce applicable diffs.
- A test generator that must reference existing symbols.
- An auto-grader whose output must map to specific input items.

Any time the model's output must satisfy a referential or structural
invariant against data the caller controls, the prompt + validator
pattern applies. The validator is the source of truth; the prompt is
a suggestion to the model.

## When this rule does NOT apply

- Free-form summarization, explanation, or code commentary where there
  is no invariant to enforce.
- Cases where the caller is happy to use whatever the model returns
  (e.g., creative writing, brainstorming). There's no referent to
  validate against.
