# Rule: Split test-helper `assert_*` methods off the pure-data class

When a dataclass accumulates test-helper methods (`assert_contains`,
`assert_has_entries`, etc.) that no non-test caller ever uses, move
those methods to a composition wrapper in a separate module. The data
class stays a pure container — no behavior, just fields — and tests
opt into the helpers by constructing the wrapper around a loaded
result. The split keeps the public API of the data class small and
prevents the `assert_*` surface from leaking into non-test code.

## The pattern

The data class stays a plain dataclass:

```python
# src/clauditor/runner.py — pure data, no methods beyond @property.
@dataclass
class SkillResult:
    """Captured output from a skill run.

    Pure data container: the Layer 1 ``assert_*`` test helpers live on
    :class:`clauditor.asserters.SkillAsserter`, which composes a
    ``SkillResult``. Non-test callers get a methodless dataclass; tests
    opt into the helpers by constructing ``SkillAsserter(result)``.
    """

    output: str
    exit_code: int
    skill_name: str
    args: str
    # ... other data fields ...

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and self.output.strip() != ""
```

The test-helper class is a thin composition wrapper in a sibling module:

```python
# src/clauditor/asserters.py — composition wrapper exposing assert_*.
class SkillAsserter:
    """Composition wrapper exposing Layer 1 assertions against a ``SkillResult``."""

    __slots__ = ("result",)

    def __init__(self, result: SkillResult) -> None:
        self.result = result

    def assert_contains(self, value: str) -> None:
        res = assert_contains(self.result.output, value)
        # ``AssertionResult.__bool__`` returns ``self.passed``, so
        # ``if not res`` tests the failure case. ``res.message`` is
        # always populated regardless of outcome.
        if not res:
            raise AssertionError(res.message)

    # ... other assert_* methods, each delegating to a pure function in
    # clauditor.assertions and raising AssertionError on failure ...


def assert_from(result: SkillResult) -> SkillAsserter:
    """Convenience factory: ``assert_from(result).assert_contains(...)``."""
    return SkillAsserter(result)
```

Both are re-exported from `clauditor.__init__` so callers keep a
single import line:

```python
# src/clauditor/__init__.py
from clauditor.asserters import SkillAsserter, assert_from
from clauditor.runner import SkillResult, SkillRunner

__all__ = [..., "SkillAsserter", "SkillResult", "assert_from", ...]
```

## Why this shape

- **Public API discipline**: non-test callers (grading pipelines,
  baseline phase, history aggregators, sidecar writers) never need the
  `assert_*` surface. Leaving those methods on `SkillResult` pollutes
  autocomplete, tempts downstream code to call test helpers for
  production control flow, and blurs the "pure data" contract.
  Keeping them on a separate class makes the split explicit at the
  import site.
- **Dataclass stays serialization-friendly**: with no methods beyond
  `@property`, `SkillResult` can be passed to sidecar writers,
  `dataclasses.asdict`, or any generic data-walker without worrying
  about behavior slipping into the serialized form. Future fields
  (like `warnings: list[str]` from US-007) land as pure data with no
  method-level coupling.
- **Tests stay terse**: the wrapper constructor is one line
  (`asserter = SkillAsserter(result)`) and the factory function
  `assert_from(result)` reads naturally at the call site
  (`assert_from(result).assert_contains(...)`). A fluent style is
  available when a test wants it, and a stored-wrapper style is
  available when multiple assertions share a result.
- **Fixture factories keep the ergonomics**: the pytest plugin
  registers `clauditor_asserter` as a factory fixture
  (`asserter = clauditor_asserter(result)`), so tests never import
  the class directly and the wrapping happens at one seam.
- **Both names exported from `clauditor.__init__`**: callers get a
  single import surface. A test importing `SkillResult` knows it can
  also import `SkillAsserter` and `assert_from` from the same
  module; there is no "which submodule lives where" discovery cost.
- **`__slots__` on the wrapper**: `SkillAsserter` has one field
  (`result`) and `__slots__ = ("result",)` keeps the wrapper light
  — each test that constructs one is creating a bare object with
  no per-instance dict. A `SkillResult` with 11 fields carrying a
  big transcript costs enough memory on its own; the wrapper should
  not add to it.

## Canonical implementation

`src/clauditor/runner.py::SkillResult` — pure dataclass, no
`assert_*` methods, a single `succeeded` property.

`src/clauditor/asserters.py::SkillAsserter` + `assert_from` — the
composition wrapper + convenience factory. All `assert_*` methods
delegate to the pure functions in `clauditor.assertions`
(`assert_contains`, `assert_regex`, `assert_has_entries`, …) and
raise `AssertionError` on failure with the assertion's `message`.

Call sites:

- `src/clauditor/pytest_plugin.py::clauditor_asserter` — factory
  fixture that wraps a `SkillResult` in a `SkillAsserter`. Tests use
  it as `asserter = clauditor_asserter(result)` so the wrapping
  happens at one point and the fixture signature stays stable even
  if the wrapper class grows new methods.
- `tests/test_runner.py::TestSkillAsserter` — canonical test-shape
  reference. Each `assert_*` method has a positive and a negative
  test; `test_assert_from_factory` verifies `assert_from(result)`
  returns a `SkillAsserter` instance.

Re-export: `src/clauditor/__init__.py` lists both `SkillAsserter`
and `assert_from` in `__all__` alongside `SkillResult` so callers
get a single import line.

Traces to bead `clauditor-24h.6` (US-006) of
`plans/super/audit-quality-2026-04.md`.

## When this rule applies

When a data class's method surface is used only from a narrow caller
category — typically tests, but also CLI-only helpers or repl-only
utilities — split the methods off into a composition wrapper in a
sibling module. The trigger is "who actually calls these methods":
if the honest answer is "only tests" or "only one caller type",
extract.

Sibling patterns this rule generalizes to:

- `SkillResult` + `SkillAsserter` (tests) — canonical.
- A future `GradingReport` + `GradingReportFormatter` (CLI
  rendering) if `to_markdown` / `to_table` methods start landing on
  the report dataclass.
- A future `SkillSpec` + `SkillSpecInspector` (debug/repl) if
  diagnostic helpers accumulate on the spec class.

## When this rule does NOT apply

- Methods the data class legitimately needs for its own invariants
  (`__post_init__`, `@property` computed fields, equality/ordering
  dunders). Those belong on the dataclass.
- A `to_json()` / `from_json()` round-trip. Serialization is a
  method of the data class itself (see
  `.claude/rules/json-schema-version.md` — writer owns the version
  field). Do not split serialization into a wrapper.
- Cases where the method surface is used by *every* caller, not
  just a narrow category. If `assert_contains` were called from
  production grading code, the split would create friction without
  benefit.
- Small result classes with one or two helpers that are genuinely
  general-purpose. The split has a cost (two imports, two files); do
  not pay it for a trivially small API.
