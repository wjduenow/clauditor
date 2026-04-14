# Rule: Per-iteration JSON sidecars are written during workspace staging

Any new per-iteration JSON artifact — `assertions.json`, `extraction.json`,
`baseline_*.json`, etc. — must be written inside the staging directory
(`workspace.tmp_path`) BEFORE `workspace.finalize()` runs the atomic rename.
Post-finalize writes race with concurrent peers, break the atomic publication
guarantee, and cannot roll back on failure.

## The pattern

```python
with allocate_iteration(...) as workspace:
    try:
        skill_dir = workspace.tmp_path
        # ... run skill, grade, extract ...

        # All sidecars land in the staging dir — atomic publication.
        (skill_dir / "grading.json").write_text(grading.to_json())
        (skill_dir / "assertions.json").write_text(json.dumps(assertions))
        if spec.sections:
            (skill_dir / "extraction.json").write_text(extraction.to_json())
        if args.baseline:
            _run_baseline_phase(spec, skill_dir, ...)  # writes baseline_*.json

        workspace.finalize()  # atomic rename iteration-N-tmp → iteration-N
    except Exception:
        workspace.abort()     # staging dir deleted, no partial publication
        raise
```

## Why this shape

- **Atomic publication**: `workspace.finalize()` is a single POSIX `rename`
  call on the parent dir. Either every sidecar is visible under
  `iteration-N/<skill>/`, or none are.
- **Rollback on exception**: any failure after writing some sidecars falls
  through to `workspace.abort()` in the `finally` block, deleting the
  staging dir entirely. There is no "partially published iteration" state.
- **Concurrent peer safety**: two `clauditor grade` processes may race on
  the same iteration number. The rename-based publication detects the race
  and raises `IterationExistsError` cleanly; a post-finalize write would
  instead corrupt whichever peer won.

## What NOT to do

- Do NOT append files to `iteration-N/<skill>/` after `workspace.finalize()`.
- Do NOT open files for writing outside `workspace.tmp_path` while the
  workspace is active.
- Do NOT mutate the rendered `iteration-N/` dir after publication — it is
  treated as immutable by downstream readers (audit, compare, trend).

## Canonical implementation

`src/clauditor/cli.py` — `_cmd_grade_with_workspace` persistence slot inside
the `if not only_criterion:` block, just before `workspace.finalize()`.
`_run_baseline_phase` follows the same contract for `baseline_*.json`.

## When this rule applies

Any new per-iteration on-disk artifact that downstream code expects to read
from `iteration-N/<skill>/`. Logs, transient debug dumps, or one-off test
fixtures do not belong in the iteration dir and are not covered.
