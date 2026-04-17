# Rule: Home-directory exclusion for `.claude` marker walks

Any helper that walks up the filesystem looking for a project root by
checking for a `.claude/` marker directory MUST explicitly exclude
the user's home directory (`Path.home()`) from matching on that
specific marker. Claude Code itself ships a `~/.claude/` user config
dir on every developer workstation, so without the exclusion any
walk that starts under `$HOME` with no intermediate project marker
silently ascends to `$HOME` — and the caller then treats the user's
global Claude Code config as "the project root," contaminating
unrelated work.

This is a class of bug, not a one-off. Any new helper that accepts
`.claude/` as a marker inherits the hazard.

## The trap

```python
# WRONG — ascends into ~/.claude for any cwd under $HOME lacking an
# intermediate project marker.
def find_project_root(cwd: Path) -> Path | None:
    current = cwd
    for _ in range(50):
        if (current / ".git").exists():
            return current
        if (current / ".claude").is_dir():  # matches ~/.claude!
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None
```

The failure is silent:

- `clauditor setup` run from `~/Downloads` (no intermediate
  `.git`/`.claude`) walks up to `$HOME`, finds `~/.claude/`, and
  installs the skill symlink at `~/.claude/skills/clauditor/` — in
  the user's Claude Code config tree, not their intended project.
- A later `clauditor setup --unlink` from inside any actual project
  appears to do nothing (cwd walks up to `$HOME` again, removes the
  symlink from `~/.claude/` but leaves the user confused about why
  their project-local install is still present).
- No exit-2 "no project root found" ever fires — the walk *did*
  find a marker, just the wrong one.

## The pattern

Resolve `Path.home()` once outside the loop (with a `try/except` for
systems where `HOME` is undefined or unreadable), compare the
resolved candidate dir to it, and skip the `.claude` marker — but
NOT `.git` — when at home:

```python
def find_project_root(cwd: Path) -> Path | None:
    try:
        home = Path.home().resolve()
    except (RuntimeError, OSError):
        home = None

    current = cwd
    for _ in range(50):
        try:
            resolved = current.resolve()
        except OSError:
            resolved = current
        at_home = home is not None and resolved == home

        if (current / ".git").exists():
            return current
        if not at_home and (current / ".claude").is_dir():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None
```

Three invariants to preserve:

- **`home` resolution happens once**, outside the loop. Calling
  `Path.home().resolve()` per iteration is wasteful and can surface
  different values if `HOME` changes mid-walk.
- **`try/except` both the home lookup and the candidate `resolve()`**.
  Broken perms along the walk path, containerized envs with no
  `HOME`, and `os.fspath` edge cases all manifest as `OSError` or
  `RuntimeError` from `Path.home()`. Fall through to "no exclusion"
  rather than crashing — a failure to detect home is not a reason
  to abort the walk.
- **Exclude `.claude` only, not `.git`**. A user who treats `$HOME`
  as a git repo is making an explicit, uncommon choice; that
  decision should still be honored. The hazard is specifically that
  `.claude` at `$HOME` is the *default* Claude Code state, not an
  intentional project marker.

## Why `.git` is different

- `~/.git/` is rare and signals "I have explicitly chosen to treat
  my home dir as a checkout." Honoring it matches user intent.
- `~/.claude/` is shipped by Claude Code on install and exists on
  essentially every developer machine that has Claude Code set up.
  Matching it contaminates user config with unrelated workspace
  data.
- The asymmetry is documented in the Claude Code install docs: the
  user config dir lives at `$HOME/.claude/` and is not meant to
  stand in for a project marker.

## Canonical implementations

- `src/clauditor/paths.py::resolve_clauditor_dir` — first anchor;
  originally solved this class of bug when clauditor's `.clauditor/`
  output dir was otherwise landing in `~/.clauditor/` for users
  running from scratch dirs under `$HOME`.
- `src/clauditor/setup.py::find_project_root` — second anchor;
  regressed by losing the home-exclusion when the module was first
  written for issue #43, then re-fixed in Quality Gate Pass 4.
  `tests/test_setup.py::TestFindProjectRoot::test_find_project_root_skips_claude_at_home`
  is the regression guard;
  `test_find_project_root_accepts_git_at_home` proves the exclusion
  is `.claude`-specific, not a blanket home-dir block.

## When this rule applies

Any new marker-walk helper whose marker set includes `.claude` (as a
directory or a file). The exclusion is load-bearing for `.claude`
specifically because it has an established meaning in `$HOME`.

## When this rule does NOT apply

- Marker walks that use only `.git` (or other project-specific
  markers like `Cargo.toml`, `pyproject.toml`, `package.json`).
  Those markers have no `$HOME`-default collision.
- Walks that are explicitly scoped to a user-provided directory
  (e.g. `--project-dir /foo`) and do not ascend beyond it. No walk
  means no hazard.
- Contexts where landing at `$HOME` is the intended behavior (e.g.
  resolving the user's config dir on purpose). Those should call
  `Path.home()` directly rather than walking a marker set.
