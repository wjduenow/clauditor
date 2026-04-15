"""A/B baseline comparator — runs skill vs raw Claude and detects regressions.

Compares skill output against a raw-Claude baseline using the same grading
rubric, flagging regressions where the baseline passes but the skill fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from clauditor.assertions import AssertionSet

FlipKind = Literal["regression", "improvement", "new", "removed"]


@dataclass
class Flip:
    """A single assertion that differs between two AssertionSets."""

    name: str
    before_passed: bool
    after_passed: bool
    kind: FlipKind


def diff_assertion_sets(
    before: AssertionSet, after: AssertionSet
) -> list[Flip]:
    """Diff two AssertionSets, returning a list of Flips sorted by name.

    - ``regression``: was passing, now failing
    - ``improvement``: was failing, now passing
    - ``new``: assertion only present in ``after``
    - ``removed``: assertion only present in ``before``

    Assertions present in both with the same ``passed`` value are omitted.
    """
    before_by_name = {r.name: r for r in before.results}
    after_by_name = {r.name: r for r in after.results}
    all_names = set(before_by_name) | set(after_by_name)

    flips: list[Flip] = []
    for name in all_names:
        b = before_by_name.get(name)
        a = after_by_name.get(name)
        if b is None and a is not None:
            flips.append(
                Flip(
                    name=name,
                    before_passed=False,
                    after_passed=a.passed,
                    kind="new",
                )
            )
        elif a is None and b is not None:
            flips.append(
                Flip(
                    name=name,
                    before_passed=b.passed,
                    after_passed=False,
                    kind="removed",
                )
            )
        elif b is not None and a is not None:
            if b.passed == a.passed:
                continue
            kind: FlipKind = (
                "regression" if b.passed and not a.passed else "improvement"
            )
            flips.append(
                Flip(
                    name=name,
                    before_passed=b.passed,
                    after_passed=a.passed,
                    kind=kind,
                )
            )
    flips.sort(key=lambda f: f.name)
    return flips
