"""Persistent metric history for clauditor grade runs.

Appends a JSON line per grade run to ``.clauditor/history.jsonl`` so the
``clauditor trend`` subcommand can render a time series + ASCII sparkline.

ASCII-only (DEC-014): the sparkline uses ``"_.-=#"`` glyphs — no Unicode
block characters.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

_DEFAULT_PATH = Path(".clauditor/history.jsonl")
SPARK_GLYPHS = "_.-=#"


def append_record(
    skill: str,
    pass_rate: float,
    mean_score: float | None,
    metrics: dict,
    path: Path = _DEFAULT_PATH,
) -> None:
    """Append one history record for a grade run.

    Creates the parent directory if it does not already exist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
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
    path: Path = _DEFAULT_PATH,
) -> list[dict]:
    """Read all history records, optionally filtered by skill.

    Missing file -> empty list. Corrupt lines are skipped with a warning to
    stderr.
    """
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
    if len(values) == 1:
        return SPARK_GLYPHS[n // 2]

    lo = min(values)
    hi = max(values)
    if hi == lo:
        return SPARK_GLYPHS[n // 2] * len(values)

    out = []
    for v in values:
        norm = (v - lo) / (hi - lo)
        idx = round(norm * (n - 1))
        idx = max(0, min(n - 1, idx))
        out.append(SPARK_GLYPHS[idx])
    return "".join(out)
