# Rule: LLM-produced dicts validate through `from_dict`, never tempfile + `from_file`

When an LLM is asked to produce a JSON payload that must load through
an existing dataclass validator (`EvalSpec.from_file`,
`SkillSpec.from_file`, etc.), route the proposed dict through an
**in-memory `from_dict(data, spec_dir=...)` classmethod** rather than
writing to a tempfile and calling `from_file(path)`. The tempfile
roundtrip couples validation to disk state, hides the `spec_dir`
resolution context, and leaves a cleanup/race-condition hazard in
every error path.

If the existing validator only exposes `from_file`, **extract
`from_dict` first** as a pure classmethod, then reduce `from_file` to
`json.load(f)` + `from_dict(data, path.parent)`. No behavior change
for existing callers; all validation error messages stay byte-
identical because the same code path produces them.

## The pattern

### Step 1 — split the loader into `from_file` (I/O) + `from_dict` (validation)

```python
# schemas.py
@classmethod
def from_file(cls, path: str | Path) -> EvalSpec:
    """Load an eval spec from a JSON file.

    Thin wrapper around :meth:`from_dict`: opens the file, decodes
    JSON, and delegates validation/construction to ``from_dict``.
    The file's parent directory is passed as ``spec_dir`` so that
    ``input_files`` path resolution (strict containment relative to
    the spec dir) matches the previous behavior.
    """
    path = Path(path)
    with path.open() as f:
        data = json.load(f)
    # Preserve the prior "missing skill_name defaults to file stem"
    # behavior by injecting into a new dict (do not mutate caller
    # data).
    if isinstance(data, dict) and "skill_name" not in data:
        data = {"skill_name": path.stem, **data}
    return cls.from_dict(data, spec_dir=path.parent.resolve())


@classmethod
def from_dict(cls, data: dict, spec_dir: Path) -> EvalSpec:
    """Construct an EvalSpec from an in-memory dict.

    ``spec_dir`` is used for ``input_files`` path resolution (strict
    containment, no absolute paths, no traversal out of ``spec_dir``).
    Raises ``ValueError`` on any structural problem in ``data``.
    """
    # ... all validation logic that used to live in from_file ...
```

### Step 2 — LLM validator collects `ValueError` messages

```python
# propose_eval.py
def validate_proposed_spec(
    spec_dict: dict, spec_dir: Path
) -> list[str]:
    """Run the proposed dict through EvalSpec.from_dict.

    Collects every ValueError message into a list the caller can
    surface verbatim. An empty list means the spec loads cleanly.
    """
    errors: list[str] = []
    try:
        EvalSpec.from_dict(spec_dict, spec_dir=spec_dir)
    except ValueError as exc:
        errors.append(str(exc))
        return errors

    # Additional semantic checks that from_dict does not enforce
    # (e.g. "at least one assertion or criterion").
    ...
    return errors
```

### Step 3 — CLI routes the `list[str]` to exit codes

```python
# cli/propose_eval.py
if report.validation_errors:
    print(f"ERROR: {len(report.validation_errors)} validation errors:")
    for msg in report.validation_errors:
        print(f"  - {msg}", file=sys.stderr)
    return 2  # per llm-cli-exit-code-taxonomy rule
```

## Why this shape

- **No tempfile, no cleanup hazard**: a tempfile-based validator must
  remember to `unlink` on every return path — success, parse error,
  validator error, API error, KeyboardInterrupt. One missed branch
  leaks files into the user's tmpdir. The `from_dict` path has no
  tempfile to clean up.
- **`spec_dir` is explicit, not implicit**: `from_file(path)` infers
  `spec_dir` from `path.parent` — fine when the spec is a real file
  the user wrote, but meaningless when the "file" is a tempfile the
  caller just synthesized. Writing to `/tmp/proposed_spec_xxx.json`
  and calling `from_file` would resolve `input_files` against
  `/tmp/`, which is not the skill's directory — silently passing
  wrong-containment paths or rejecting valid ones. `from_dict` takes
  `spec_dir` as a required parameter, so the caller specifies the
  real skill directory.
- **Existing call sites unchanged**: `from_file` stays public and
  unchanged behaviorally. Every `from_file(eval.json)` caller keeps
  working. Only the new LLM-path caller uses `from_dict`. The split
  is additive.
- **Error messages stay byte-identical**: because `from_file` now
  delegates to `from_dict` for all validation, the exact
  `ValueError` messages that users see (`"EvalSpec(skill_name=...):
  input_files[0]='...' — escapes spec directory"`) are the same
  whether the spec came from disk or from the LLM. No new error
  taxonomy, no divergent messages to maintain.
- **Non-mutating input contract holds for both**: `from_file` injects
  the `skill_name` default into a **new** dict rather than mutating
  the loaded JSON, so `from_dict` still receives an untouched caller
  dict. The LLM path benefits from the same discipline: the
  validator does not mutate the LLM's response dict, so the caller
  can also write that dict verbatim to the sidecar.
- **Composes with `.claude/rules/pre-llm-contract-hard-validate.md`**:
  that rule governs *what* invariants the parser should enforce on
  LLM output; this rule governs *how* the validation plumbing should
  be wired (in-memory, not via tempfile). They stack: the validator
  that `pre-llm-contract-hard-validate` describes lives inside the
  `from_dict` path this rule prescribes.

## What NOT to do

- Do NOT write the LLM's proposed JSON to a tempfile and call
  `from_file(tempfile)`. You inherit cleanup hazards, wrong
  `spec_dir` resolution, and a pointless serialize/deserialize
  roundtrip.
- Do NOT duplicate validation logic in the LLM-path module by
  re-implementing `EvalSpec.from_dict` checks inline. When a future
  validation rule lands in `from_dict` (a new field, a tighter
  containment check), the duplicate silently drifts.
- Do NOT let `from_dict` accept `spec_dir=None` and silently fall
  through to `Path.cwd()` — `spec_dir` is the load-bearing
  containment anchor for any path-bearing fields. If callers do not
  have a real directory, they should construct one (e.g. the skill's
  own dir) explicitly.

## Canonical implementation

Writer split: `src/clauditor/schemas.py::EvalSpec.from_file` (now a
~10-line wrapper) + `src/clauditor/schemas.py::EvalSpec.from_dict`
(the ~270-line validator that used to live inside `from_file`).

LLM-path caller: `src/clauditor/propose_eval.py::validate_proposed_spec`
— single try/except wrapping `EvalSpec.from_dict`, collects
`ValueError` messages into a `list[str]`. The async orchestrator
`propose_eval()` calls this helper with
`spec_dir=skill_md_path.parent` so the LLM's `input_files` resolve
against the real skill directory, not a tempfile location.

CLI routing: `src/clauditor/cli/propose_eval.py` — non-empty
`validation_errors` → exit 2 (per the exit-code taxonomy rule;
DEC-006 in `plans/super/52-propose-eval.md`).

Traces to bead `clauditor-2ri` epic #52, DEC-007 of
`plans/super/52-propose-eval.md`.

## When this rule applies

Any new feature whose LLM produces a structured payload that must
pass through an existing on-disk-file dataclass loader:

- A proposer that emits a replacement eval.json, skill.yaml,
  rubric.json, etc.
- A critic that emits a proposed config patch to be re-validated
  against the same loader.
- A regeneration loop that feeds a proposed spec back through the
  validator before committing.

If the existing loader is `from_file(path)`-only, extract
`from_dict(data, spec_dir)` first, reduce `from_file` to
`json.load + from_dict`, and call `from_dict` from the LLM path.

## When this rule does NOT apply

- Loaders where `spec_dir` has no meaning (no path-bearing fields,
  no relative containment) and the whole validator fits in a pure
  function over the dict. Those never needed a `from_file` wrapper
  in the first place.
- One-off validations that are genuinely file-specific (e.g.
  checking a file's `stat()` mtime or inode). Those legitimately
  need a real path.
- Test fixtures that use `tmp_path` as the real spec directory. The
  loader is still `from_file`, the path is real, and `spec_dir`
  resolution against `tmp_path` is correct. This rule is about LLM
  paths, not tests.
