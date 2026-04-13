"""Persistent metric history for clauditor grade runs.

Appends a JSON line per grade run to ``.clauditor/history.jsonl`` so the
``clauditor trend`` subcommand can render a time series + ASCII sparkline.

ASCII-only (DEC-014): the sparkline uses ``"_.-=#"`` glyphs — no Unicode
block characters.

Schema v2 (US-004): each record is a JSON object with the following
top-level keys:

- ``schema_version``: int, always ``2``
- ``command``: one of ``"grade"``, ``"extract"``, ``"validate"``
- ``ts``: ISO-8601 UTC timestamp
- ``skill``: skill name
- ``pass_rate``: float or None
- ``mean_score``: float or None
- ``metrics``: dict (canonical bucketed metrics from ``clauditor.metrics``)

v1 records (no ``schema_version``, no ``command``) may still exist on disk
and are returned as-is by :func:`read_records`.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

_DEFAULT_PATH = Path(".clauditor/history.jsonl")
SPARK_GLYPHS = "_.-=#"
SCHEMA_VERSION = 2


def append_record(
    skill: str,
    pass_rate: float,
    mean_score: float | None,
    metrics: dict,
    *,
    command: Literal["grade", "extract", "validate"],
    path: Path | None = None,
) -> None:
    """Append one history record for a grade run.

    Creates the parent directory if it does not already exist.
    ``path`` defaults to :data:`_DEFAULT_PATH` resolved at call time, so
    tests can monkeypatch the module attribute.

    ``command`` is required (keyword-only) and identifies which clauditor
    subcommand produced the record. Every written record includes
    ``schema_version: 2`` at the top level.
    """
    if path is None:
        path = _DEFAULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "ts": datetime.now(UTC).isoformat(),
        "skill": skill,
        "pass_rate": pass_rate,
        "mean_score": mean_score,
        "metrics": metrics,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def read_records(
    skill: str | None = None,
    path: Path | None = None,
) -> list[dict]:
    """Read all history records, optionally filtered by skill.

    Missing file -> empty list. Corrupt lines are skipped with a warning to
    stderr. ``path`` defaults to :data:`_DEFAULT_PATH` resolved at call
    time so tests can monkeypatch the module attribute.
    """
    if path is None:
        path = _DEFAULT_PATH
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
            if skill is not None and record.get("skill") != skill:
                continue
            records.append(record)
    return records


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
