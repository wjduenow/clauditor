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

## Canonical implementation

`src/clauditor/schemas.py` — `EvalSpec.from_file()`, the `input_files`
validation block. Reuse this recipe verbatim for any new path-bearing field.
