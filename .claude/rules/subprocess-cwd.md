# Rule: CWD override on subprocess wrappers

When a long-lived subprocess wrapper (like `SkillRunner`) needs to let
callers swap the working directory for a single invocation without
disturbing existing call sites, use a keyword-only `cwd` parameter that
defaults to the wrapper's configured project dir.

## The pattern

```python
class SkillRunner:
    def __init__(self, project_dir: Path, ...):
        self.project_dir = project_dir

    async def run(
        self,
        prompt: str,
        *,
        cwd: Path | None = None,
        ...,
    ) -> RunResult:
        effective_cwd = cwd if cwd is not None else self.project_dir
        # ... pass effective_cwd into the subprocess spawn ...
```

## Why this shape

- **Keyword-only** (`*,`): forces call sites to be explicit and prevents
  positional-argument ambiguity if the signature grows.
- **`None` sentinel + default-to-self.project_dir**: every pre-existing
  call site keeps working with zero changes. Only new callers that need
  staging dirs, tempdirs, or sibling worktrees pass `cwd=`.
- **Resolution at call time, not init**: the wrapper is reusable across
  many runs against different CWDs (e.g. variance reps in different
  staging dirs).

## Canonical implementation

`src/clauditor/runner.py` — `SkillRunner.run()`. Caller example: see
`spec.py` where `effective_cwd` (staging dir when `input_files` are
declared) is threaded through to `runner.run(..., cwd=effective_cwd)`.
