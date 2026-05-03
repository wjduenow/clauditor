# Rule: Autouse-pinned `shutil.which` couples every PATH-based resolver

When a `tests/conftest.py` autouse fixture patches `shutil.which`
(typically to `lambda name: None`) on a singleton module attribute
to make one PATH-sensitive resolver deterministic, the patch
**globally clobbers every other reader of `shutil.which`** — including
future PATH-based resolvers added long after the autouse pin
shipped. The trap is that the symptom appears far from the cause:
the new resolver fails on every test in the suite under its default
"auto" mode, and the failure has no obvious link to the `tests/conftest.py`
fixture that has been working fine for months.

The fix is **NOT** to remove or scope-tighten the original `which`
pin (other tests rely on its determinism). The fix is to add a
**parallel autouse pin at a higher precedence layer** — typically
an environment variable that the new resolver consults *before*
falling through to its `shutil.which` branch. The env-var pin
short-circuits the resolver above the PATH-lookup, so the global
`which → None` patch never matters.

## The trap

```python
# tests/conftest.py — autouse fixture, shipped by feature A.
@pytest.fixture(autouse=True)
def _force_api_transport_in_tests(monkeypatch):
    """Force ``call_anthropic(transport="auto")`` to resolve to API."""
    import clauditor._anthropic as _anthropic
    # ``shutil.which`` is a module-level attribute on the singleton
    # ``shutil`` module — patching it here affects EVERY reader.
    monkeypatch.setattr(_anthropic.shutil, "which", lambda name: None)
```

Months later, feature B adds a second PATH-based resolver:

```python
# src/clauditor/_providers/__init__.py — feature B's resolver.
def resolve_harness(env, spec, ...) -> str:
    # ... CLI flag, env var, spec field precedence layers ...
    # Final layer: auto-detect from PATH.
    if shutil.which("claude") is not None:
        return "claude-code"
    if shutil.which("codex") is not None:
        return "codex"
    raise ValueError("no harness binary on PATH")
```

Every test in the suite that exercises a code path touching
`resolve_harness` under the default `"auto"` precedence value
now raises `ValueError`, with a stack trace that points at the
new resolver — NOT at the autouse fixture two release-cycles old
that silently made the `which` lookup return `None`.

## The fix

Add a parallel autouse pin at the env-var precedence layer the new
resolver consults BEFORE falling through to PATH:

```python
@pytest.fixture(autouse=True)
def _force_api_transport_in_tests(monkeypatch):
    import clauditor._anthropic as _anthropic
    monkeypatch.setattr(_anthropic.shutil, "which", lambda name: None)
    # NEW: pin the env-var layer so the harness resolver
    # short-circuits at a higher precedence layer before the
    # auto-PATH-lookup branch fires.
    monkeypatch.setenv("CLAUDITOR_HARNESS", "claude-code")
```

Tests that legitimately want to exercise the auto-PATH-lookup branch
(e.g. tests for the no-binary-on-PATH error path, or for the env-var
layer's own behavior) override the autouse default inline:

```python
def test_auto_resolves_codex_when_claude_absent(monkeypatch):
    monkeypatch.delenv("CLAUDITOR_HARNESS", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/codex" if name == "codex" else None)
    assert resolve_harness(...) == "codex"
```

## Why this is non-obvious

- **Singleton patch, distant symptom.** `shutil.which` is one
  attribute on one module. Patching it inside an autouse fixture
  for feature A is invisible to a contributor adding feature B's
  resolver — they have no signal that PATH is mocked. The first
  test run with the new resolver fails everywhere at once, with
  no obvious link to `conftest.py`.
- **The fix lives outside the new feature.** A contributor
  diagnosing the failure naturally looks at the new resolver's
  code, not at `tests/conftest.py` autouse machinery shipped by
  an unrelated subsystem. The fix is a one-line `monkeypatch.setenv`
  in conftest, not a code change in the resolver.
- **You cannot tighten the original `which` pin.** It is autouse
  precisely because every test needs the determinism — narrowing
  it to "only tests that exercise transport" would require
  auditing the entire suite. Layering a parallel pin is cheaper
  and safer.
- **Parallel pins must target the precedence layer above PATH.**
  Adding another `which` patch achieves nothing; the original
  already returns `None`. The pin must work *one layer up* — env
  var, CLI flag, spec field — so the resolver short-circuits
  before the (already-pinned) PATH lookup matters.

## Canonical example

`tests/conftest.py::_force_api_transport_in_tests` (lines ~733-757)
combines the original `monkeypatch.setattr(_anthropic.shutil, "which",
...)` pin (from #86) with the parallel
`monkeypatch.setenv("CLAUDITOR_HARNESS", "claude-code")` pin (from
#151 US-005). The harness resolver is
`src/clauditor/_providers/__init__.py::resolve_harness` (4-layer
precedence: CLI > env > spec > PATH-auto). The env-var pin
short-circuits at layer 2, so the `shutil.which("claude")` lookup at
layer 4 never fires under the autouse default.

The harness anchor in `.claude/rules/spec-cli-precedence.md` documents
the resolver's precedence shape; this rule documents the test-infra
coupling that the resolver inherited at integration time.

## When this rule applies

Any future feature that:

- Adds a new PATH-based resolver via `shutil.which` to production
  code, AND
- The resolver has a default value that triggers the PATH-auto
  branch under normal test conditions (e.g. `transport="auto"`,
  `harness="auto"`).

Before merging the new resolver, audit `tests/conftest.py` for
existing autouse `shutil.which` patches. If one exists, add a
parallel autouse pin at the precedence layer immediately above the
new resolver's PATH lookup.

The rule generalizes to any autouse-pinned global singleton (not
just `shutil.which`): when an autouse fixture clobbers a
process-wide default to make one resolver deterministic, every
future resolver that consults the same default inherits the patch
and needs a parallel-layer pin to opt out.

## When this rule does NOT apply

- Tests that explicitly want to exercise the auto-PATH-lookup
  branch. Those tests should `monkeypatch.delenv` the parallel-
  layer pin and `monkeypatch.setattr(shutil, "which", ...)` to a
  test-specific stub inside the test body — overriding both the
  autouse env pin AND the autouse `which` pin locally.
- Production-code resolvers that read `shutil.which` from a
  module the autouse fixture does NOT touch. The original pin
  patches a specific module's `shutil` attribute (e.g.
  `clauditor._anthropic.shutil.which`); a resolver in an
  unpatched module is unaffected.
- Diagnostic scripts in `scripts/` that bypass pytest entirely.
  Autouse fixtures only fire under pytest collection.
