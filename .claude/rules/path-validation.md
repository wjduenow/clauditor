# Rule: Validating user-provided filesystem paths

When an `EvalSpec` field (or any other config-loaded field) accepts a
filesystem path from a user-authored JSON/YAML file, validate it with the
following recipe. This is a tight, security-positive check that rejects
absolute paths, `..` traversal, symlink-target escape, directories, sockets,
FIFOs, and broken symlinks in one go.

## The pattern

```python
spec_dir = path.parent.resolve()

if not isinstance(entry, str) or entry == "":
    raise ValueError(f"{field}[{i}]={entry!r} — must be a non-empty string")
if Path(entry).is_absolute():
    raise ValueError(f"{field}[{i}]={entry!r} — absolute paths not allowed")
try:
    candidate = (path.parent / entry).resolve(strict=True)
except FileNotFoundError as e:
    raise ValueError(
        f"{field}[{i}]={entry!r} — file not found under {spec_dir}"
    ) from e
if not candidate.is_relative_to(spec_dir):
    raise ValueError(f"{field}[{i}]={entry!r} — escapes spec directory")
if not candidate.is_file():
    raise ValueError(f"{field}[{i}]={entry!r} — not a regular file")
```

## Why each step matters

- `isinstance(entry, str)` + non-empty: rejects nulls, numbers, lists, and
  empty strings before any filesystem call.
- `is_absolute()`: blocks `/etc/passwd`-style escapes before resolution.
- `resolve(strict=True)`: normalizes `..`, follows symlinks to their real
  target, AND raises `FileNotFoundError` if the target does not exist. This
  is the load-bearing step — without `strict=True` you would silently accept
  dangling symlinks.
- `is_relative_to(spec_dir)`: containment check that runs against the
  *resolved* target, so a symlink pointing outside the spec dir is rejected
  even when the symlink itself lives inside.
- `is_file()`: rejects directories (including a `"."` entry), sockets,
  FIFOs, character devices, etc. Combined with the strict resolve, also
  rejects broken symlinks.

## Multi-anchor variant: searching multiple containment roots

When a resolver consults more than one anchor (e.g. "look in
`<skill-dir>` first, then fall back to `<project-root>`"), apply
the recipe to **each** anchor independently — never relax it for
the fallback tier. Anchors are typed by which containment root the
candidate must satisfy; a candidate found via the second tier
must still `is_relative_to` the second-tier anchor (NOT the first).
A hostile symlink in the first-tier directory pointing outside its
anchor is a security signal, not an excuse to silently fall through
to the second tier — let the `ValueError` propagate.

The shape collapses to one helper that loops over `(anchor, candidate)`
pairs:

```python
def resolve_first_match(
    candidates: list[tuple[Path, Path]],
) -> Path | None:
    """For each (anchor, candidate) pair, validate via the recipe;
    return the first resolved path. Raises on escape from any
    visited anchor; returns ``None`` if no candidate file exists."""
    for anchor, candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            anchor_resolved = anchor.resolve(strict=True)
        except FileNotFoundError:  # pragma: no cover — defensive
            continue
        if not resolved.is_relative_to(anchor_resolved):
            raise ValueError(
                f"{candidate} resolves to {resolved!r} which "
                f"escapes anchor {anchor_resolved!r}"
            )
        return resolved
    return None
```

The order of `candidates` is the precedence order — first valid
file wins. The fail-loud-on-escape posture means a poisoned
first-tier anchor blocks the resolver entirely; this is the
correct security behavior because falling through would silently
accept the second tier as if the first didn't exist.

## Canonical implementation

`src/clauditor/schemas.py` — `EvalSpec.from_file()`, the `input_files`
validation block. Reuse this recipe verbatim for any new path-bearing field.

`src/clauditor/paths.py::resolve_agents_md` — multi-anchor variant
introduced by #154 DEC-009. Searches `<skill-dir>/AGENTS.md` first,
then `<project-root>/AGENTS.md`, with the recipe applied to each
anchor independently. Symlink-escape from either tier raises
`ValueError`; absent both tiers returns `None` for the caller to
fall through to its own default.
