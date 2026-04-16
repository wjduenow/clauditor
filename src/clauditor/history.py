"""Persistent metric history for clauditor grade runs.

Appends a JSON line per grade run to ``.clauditor/history.jsonl`` so the
``clauditor trend`` subcommand can render a time series + ASCII sparkline.

ASCII-only (DEC-014): the sparkline uses ``"_.-=#"`` glyphs — no Unicode
block characters.

Each record is a JSON object with the following top-level keys:

- ``schema_version``: int, always ``1``
- ``command``: one of ``"grade"``, ``"extract"``, ``"validate"``
- ``ts``: ISO-8601 UTC timestamp
- ``skill``: skill name
- ``pass_rate``: float or None
- ``mean_score``: float or None
- ``metrics``: dict (canonical bucketed metrics from ``clauditor.metrics``)
- ``iteration``: int or None — Ralph iteration number, when known
- ``workspace_path``: str or None — workspace dir for this run, when known

``iteration`` and ``workspace_path`` are always written, even when
``None``, so the on-disk record shape is predictable.

Concurrent appends from multiple processes are serialized via a
``fcntl.flock`` exclusive lock on ``<history_dir>/.lock``.
"""

from __future__ import annotations

import contextlib
import json
import math
import sys

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    # fcntl is POSIX-only. On Windows we fall back to a no-op lock so
    # the module can still be imported; concurrent history appends may
    # theoretically interleave but the rest of clauditor continues to
    # work.
    class _FcntlFallback:
        LOCK_EX = 0
        LOCK_UN = 0

        @staticmethod
        def flock(_fd: int, _operation: int) -> None:
            return None

    fcntl = _FcntlFallback()  # type: ignore[assignment]
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from clauditor.paths import resolve_clauditor_dir

# Module-level override, monkeypatched by tests. When ``None``, the
# default path is resolved lazily via :func:`resolve_clauditor_dir` so
# the lookup honors the caller's current working directory.
_DEFAULT_PATH: Path | None = None


def _default_path() -> Path:
    if _DEFAULT_PATH is not None:
        return _DEFAULT_PATH
    return resolve_clauditor_dir() / "history.jsonl"


SPARK_GLYPHS = "_.-=#"
SCHEMA_VERSION = 1

_FLOCK_UNSUPPORTED_WARNED = False


@contextlib.contextmanager
def _file_lock(lock_path: Path) -> Iterator[None]:
    """Acquire an exclusive ``fcntl.flock`` on ``lock_path``.

    Creates the lockfile (and its parent dir) if missing. The lock is
    released when the context exits. Use this only to serialize the
    short ``history.jsonl`` append — do not hold it across other work.

    On filesystems that do not implement advisory locking (WSL2
    ``/mnt/*`` drvfs, some NFSv3 mounts, 9p shares) ``fcntl.flock``
    raises ``OSError`` (typically ``ENOLCK`` or ``EINVAL``). Rather
    than losing the history append entirely we fall back to an
    unlocked context and warn once per process — concurrent appends
    may theoretically interleave, but a single-writer history is still
    better than silent data loss.
    """
    global _FLOCK_UNSUPPORTED_WARNED
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # ``open(..., "a")`` creates the file if missing without truncating
    # an existing one and gives us a writable fd flock can lock.
    with lock_path.open("a") as lock_fd:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            if not _FLOCK_UNSUPPORTED_WARNED:
                print(
                    f"WARNING: fcntl.flock unsupported on {lock_path} "
                    f"({exc}); history appends will be unlocked",
                    file=sys.stderr,
                )
                _FLOCK_UNSUPPORTED_WARNED = True
            yield
            return
        try:
            yield
        finally:
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def append_record(
    skill: str,
    pass_rate: float | None,
    mean_score: float | None,
    metrics: dict,
    *,
    command: Literal["grade", "extract", "validate"],
    path: Path | None = None,
    iteration: int | None = None,
    workspace_path: str | None = None,
) -> None:
    """Append one history record for a grade run.

    Creates the parent directory if it does not already exist.
    ``path`` defaults to :data:`_DEFAULT_PATH` resolved at call time, so
    tests can monkeypatch the module attribute.

    ``command`` is required (keyword-only) and identifies which clauditor
    subcommand produced the record.

    Concurrent appends from multiple processes are serialized via an
    ``fcntl.flock`` exclusive lock on ``<history_parent>/.lock`` so each
    JSONL line is written atomically without interleaving.
    """
    if command not in ("grade", "extract", "validate"):
        raise ValueError(
            f"command must be one of 'grade', 'extract', 'validate'; got {command!r}"
        )
    if path is None:
        path = _default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "ts": datetime.now(UTC).isoformat(),
        "skill": skill,
        "pass_rate": pass_rate,
        "mean_score": mean_score,
        "metrics": metrics,
        "iteration": iteration,
        "workspace_path": workspace_path,
    }
    line = json.dumps(record) + "\n"
    lock_path = path.parent / ".lock"
    with _file_lock(lock_path):
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def _check_schema_version(data: dict, source: Path, lineno: int) -> bool:
    """Return ``True`` if ``data`` has the expected schema version.

    Records with a mismatched version are skipped with a stderr warning
    per ``.claude/rules/json-schema-version.md``.
    """
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        print(
            f"warning: {source} line {lineno} has schema_version={version!r}, "
            f"expected {SCHEMA_VERSION} — skipping",
            file=sys.stderr,
        )
        return False
    return True


def read_records(
    skill: str | None = None,
    path: Path | None = None,
) -> list[dict]:
    """Read all history records, optionally filtered by skill.

    Missing file -> empty list. Corrupt lines and records with an unexpected
    ``schema_version`` are skipped with a warning to stderr. ``path``
    defaults to :data:`_DEFAULT_PATH` resolved at call time so tests can
    monkeypatch the module attribute.
    """
    if path is None:
        path = _default_path()
    if not path.exists():
        return []

    records: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                print(
                    f"WARNING: skipping corrupt history line {lineno} "
                    f"in {path}: {e}",
                    file=sys.stderr,
                )
                continue
            if not isinstance(record, dict):
                print(
                    f"WARNING: skipping non-object history line {lineno} "
                    f"in {path}",
                    file=sys.stderr,
                )
                continue
            if not _check_schema_version(record, path, lineno):
                continue
            if skill is not None and record.get("skill") != skill:
                continue
            records.append(record)
    return records


_TOP_LEVEL_KEYS = ("pass_rate", "mean_score")


def resolve_path(record: dict, path: str) -> float | int | None:
    """Resolve a dotted metric path against a history record.

    Rules:

    - ``pass_rate`` and ``mean_score`` resolve at the top level of the
      record (not inside ``metrics``).
    - Any other path (including ``duration_seconds``) is walked as dotted
      keys inside ``record["metrics"]``.

    Returns the numeric value or ``None`` if any intermediate key is
    missing or the final value is not int/float. Never raises.
    """
    if not isinstance(record, dict):
        return None

    if path in _TOP_LEVEL_KEYS:
        value = record.get(path)
    else:
        node: object = record.get("metrics")
        if not isinstance(node, dict):
            return None
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        value = node

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def collect_metric_paths(record: dict) -> set[str]:
    """Return every dotted metric path in ``record`` that resolves numeric.

    Top-level ``pass_rate``/``mean_score`` are included when numeric.
    Every numeric leaf inside ``record["metrics"]`` is emitted with its
    dotted path (e.g. ``grader.input_tokens``, ``total.total``,
    ``duration_seconds``).
    """
    paths: set[str] = set()
    if not isinstance(record, dict):
        return paths

    for key in _TOP_LEVEL_KEYS:
        value = record.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            paths.add(key)

    metrics = record.get("metrics")
    if isinstance(metrics, dict):
        _walk_numeric(metrics, (), paths)
    return paths


def _walk_numeric(
    node: dict, prefix: tuple[str, ...], out: set[str]
) -> None:
    for key, value in node.items():
        if not isinstance(key, str):
            continue
        next_prefix = prefix + (key,)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out.add(".".join(next_prefix))
        elif isinstance(value, dict):
            _walk_numeric(value, next_prefix, out)


def sparkline(values: list[float]) -> str:
    """Render a list of values as an ASCII sparkline.

    Uses the 5-glyph set ``"_.-=#"``. Empty input returns ``""``. A single
    value returns the middle glyph.
    """
    if not values:
        return ""
    n = len(SPARK_GLYPHS)
    mid = SPARK_GLYPHS[n // 2]
    # Replace non-finite (nan/inf) with the midpoint glyph so bad data
    # never crashes the renderer.
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return mid * len(values)
    if len(finite) == 1 and len(values) == 1:
        return mid

    lo = min(finite)
    hi = max(finite)
    if hi == lo:
        return mid * len(values)

    out = []
    for v in values:
        if not math.isfinite(v):
            out.append(mid)
            continue
        norm = (v - lo) / (hi - lo)
        idx = round(norm * (n - 1))
        idx = max(0, min(n - 1, idx))
        out.append(SPARK_GLYPHS[idx])
    return "".join(out)
