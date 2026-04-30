# Rule: Back-compat shim discipline when extracting a sibling package

When a single module (`X.py`) is split into a sibling package
(`X_pkg/__init__.py` + `X_pkg/_a.py` + `X_pkg/_b.py` + …) and the
original `X.py` is kept as a thin re-export shim for one release of
back-compat, three patterns are load-bearing and easy to get wrong.
Skipping any of them produces a silent failure mode: tests that
look like they pass but exercise nothing, classes that look the same
but break `except` ladders, mutable state that looks shared but
diverges after the first mutation. This rule codifies the three.

The failure modes are language-level (Python module binding
semantics), not project-specific — they recur every time a symbol
moves modules behind a shim. Protect against them once at the
extraction; reading old patches and `except` clauses cold afterwards
is harder.

## Pattern 1 — `from X import Y` frozen-copies the initial value of mutable module globals

**The trap.** A module-level boolean flag rebinds itself via
`global` inside a print-and-flip helper:

```python
# X_pkg/_a.py — canonical home of the flag.
_announced: bool = False

def announce() -> None:
    global _announced
    if _announced:
        return
    print("...", file=sys.stderr)
    _announced = True
```

A naive shim re-exports the flag:

```python
# X.py — back-compat shim (WRONG).
from X_pkg._a import _announced  # noqa: F401  ← FROZEN COPY!
from X_pkg._a import announce
```

`from X import Y` binds `Y` in the importer's namespace once at
import time. When `announce()` later runs `global _announced;
_announced = True`, the assignment rebinds `_announced` on
`X_pkg._a`, NOT on the shim — the shim's copy stays `False` forever.
Tests or code that reads `X._announced` (the shim path) sees
`False` after the announcement has fired; tests that monkeypatch
`X._announced = False` to reset don't reset the canonical flag.

**The fix.** Do NOT re-export mutable module globals. Document the
canonical access path in the shim:

```python
# X.py — back-compat shim (RIGHT).
# Mutable one-shot announcement flags are intentionally NOT re-exported
# here. ``from X import Y`` would frozen-copy the initial value into
# this module, but the helpers rebind via ``global`` — the alias here
# would silently diverge after the first call. Code that needs to read
# or reset a flag must target its canonical location in ``X_pkg._a``.
from X_pkg._a import announce  # public helper IS safe to re-export.
```

The public helper (`announce()`) is a function — re-exporting it
binds the *function object*, not its contents, so it works
correctly. The flag is the unsafe one.

This applies to any module-level mutable state: counters, caches,
lazy-init sentinels, registries that get appended to. **Functions,
classes, and immutable constants (`Final[str]`, `tuple`, etc.) are
safe to re-export.** Mutable state read or rebound via `global` is
not.

## Pattern 2 — class-identity must hold across shim and canonical seams

**The trap.** A class re-defined (rather than re-exported) in the
shim looks identical to the canonical version but is a *different
object*:

```python
# X_pkg/__init__.py — canonical.
class HelperError(Exception):
    pass

# X.py — shim (WRONG).
class HelperError(Exception):  # ← redefined, different class object
    pass
```

Every consumer site that does `except HelperError:` catches the
class it imported. A mix of shim-importers and canonical-importers
in the codebase silently splits the catch surface — an exception
raised by code that imported from canonical does NOT match an
`except` clause that imported from the shim.

**The fix.** Define the class once in the canonical module; re-export
through the shim. Add a regression test for *every* re-exported
class:

```python
def test_helper_error_class_identity(self) -> None:
    from X import HelperError as ShimClass
    from X_pkg import HelperError as CanonicalClass
    assert ShimClass is CanonicalClass
```

The `is` check (object identity) is what matters, not equality. A
single check per class — every class that crosses the shim boundary
needs its own test. **Do not skip alias classes** (`Foo = Bar`) — the
`is` invariant is still meaningful: `Foo is Bar` means the alias is
a true alias, not a subclass or wrapper.

The same identity invariant matters for dataclasses and
NamedTuples — anything used in `isinstance` checks, `except` ladders,
or `==`-by-identity comparisons.

## Pattern 3 — `monkeypatch.setattr` follows the symbol to its living module

**The trap.** A test that patched the original module continues to
patch the shim path after a symbol moves:

```python
# Pre-extraction: tests/test_X.py
with patch("X.call_y"):
    ...

# Post-extraction: production now imports `from X_pkg import call_y`,
# but the test still patches "X.call_y" — and the patch silently
# no-ops because the production code never looks up `X.call_y`.
```

`monkeypatch.setattr("X.call_y", mock)` mutates the attribute
`call_y` on the module object `X`. If production code reads
`call_y` via `from X_pkg import call_y` (resolved against `X_pkg`,
not `X`), the patch never fires. The test passes because some
*other* path through the code (e.g. a coincidental network mock,
or an unrelated assertion that happens to be true) makes the assert
hold.

**The fix.** Patch targets follow the symbol to its living module.
After moving `call_y` from `X.py` to `X_pkg/_a.py`:

```python
with patch("X_pkg._a.call_y"):  # ← canonical patch path
    ...
```

The mechanical update applies to: `monkeypatch.setattr`,
`unittest.mock.patch`, `mocker.patch`, `pytest.MonkeyPatch.setenv`-
adjacent helpers — anything that sets an attribute on a module by
string path. Grep before merging the extraction:

```
rg '"X\.[a-z_]+"|"X\."' tests/
```

Each remaining hit must be intentional (e.g. a test that
*specifically* exercises the back-compat shim's wrapper code).
Otherwise the test silently no-ops.

**Special case — dispatcher with deferred import.** When a thin
dispatcher wraps a moved symbol and you want test patches on the
canonical location to still fire, use a deferred per-call import:

```python
# X_pkg/__init__.py — dispatcher.
async def call_y(*args, **kwargs):
    # Deferred import so test patches that target
    # ``X_pkg._a.call_y`` (the canonical patch path) take effect
    # here. A direct import-bound call would resolve via the
    # ``from X_pkg._a import call_y`` binding above, which a patch
    # on ``X_pkg._a.call_y`` would NOT affect.
    from X_pkg import _a as _a_mod
    return await _a_mod.call_y(*args, **kwargs)
```

The `_a_mod.call_y` lookup happens at call time against the module
object, so a `patch("X_pkg._a.call_y", mock)` re-binding takes
effect. Per-call cost is one `sys.modules` dict lookup — invisible
against any real I/O the function does.

## Why these three patterns travel together

A back-compat shim that re-exports symbols, classes, and (mistakenly)
mutable state is producing **three different illusions**:

- The flag *looks* shared but isn't (Pattern 1).
- The class *looks* identical but isn't (Pattern 2).
- The patch *looks* like it fires but doesn't (Pattern 3).

All three failure modes are silent. The tests pass; the code "works";
production runs degrade weeks later when the divergence accumulates.
The patterns are language-level Python module-binding semantics — no
linter catches them, no type checker flags them, no runtime
exception fires.

The defensive shape is structural:

- For state: don't re-export it. Document the canonical access.
- For classes: re-export (not re-define) and write the identity
  test.
- For tests: grep for old shim paths and rewrite to canonical paths
  at the moment of extraction.

A back-compat shim that ships these three disciplines is genuinely
back-compatible. One that doesn't is a slow leak.

## Canonical implementation

Two refactors in clauditor have applied this rule end-to-end:

- `_anthropic.py` → `_providers/` package (#144). The shim
  `src/clauditor/_anthropic.py` keeps `call_anthropic` as a thin
  deprecation-announcement wrapper plus re-exports of every public
  symbol from `_providers/__init__.py` and `_providers/_anthropic.py`.
  The four `_announced_*` and `_*_TEMPLATE` mutable flags are
  intentionally NOT re-exported (see the docstring in `_anthropic.py`
  for the canonical access pattern). Class-identity tests for
  `AnthropicAuthMissingError`, `AnthropicHelperError`, `ClaudeCLIError`,
  `ModelResult`, and the `AnthropicResult is ModelResult` alias live
  in `tests/test_providers_auth.py::TestExceptionClassIdentity`.
  Test patch paths were mechanically updated from
  `clauditor._anthropic.X` to `clauditor._providers._anthropic.X` /
  `clauditor._providers._auth.X` per symbol location. The dispatcher
  `call_model` in `_providers/__init__.py` and the shim's
  `call_anthropic` both use the deferred-per-call-import pattern to
  keep canonical-location patches working.

- `runner.py::_invoke_claude_cli` → `_harnesses/` package (#148).
  Same shape, smaller surface. The `_harnesses/_claude_code.py`
  module owns the canonical implementation; `runner.py` exposes a
  thin facade.

Both refactors traced their pattern decisions to
`plans/super/144-providers-call-model.md` and
`plans/super/148-extract-harness-protocol.md` respectively.

## Companion rules

- `.claude/rules/centralized-sdk-call.md` — defines the `call_model`
  dispatcher seam this rule's canonical implementation exposes.
  Includes the "Implicit-coupling announcements — an emerging family"
  subsection that documents per-flag canonical locations
  (Pattern 1 in practice).
- `.claude/rules/rule-refresh-vs-delete.md` — the meta-rule for
  refreshing existing `.claude/rules/*.md` files when a refactor
  shifts their context. Applies whenever an extraction like the ones
  above touches a file named in another rule's "Canonical
  implementation" section.
- `.claude/rules/monotonic-time-indirection.md` — the
  module-level-alias indirection pattern is a Pattern 1 ally:
  patching `clauditor._providers._anthropic._sleep` (the canonical
  alias location) works only because `_sleep` lives in the
  canonical module and tests target it there.

## When this rule applies

Any future extraction where:

1. A single module (or substantial portion of one) is being split
   into a sibling package.
2. The original module is being kept as a back-compat shim for one
   release (typical) or longer (rare).
3. Production callers OR tests already import from the original
   module's path.

Plausible future callers in clauditor:

- `runner.py` further decomposing (after #148's harness extraction).
- A future `_pricing/` package extraction from `audit.py` if cost
  modeling grows.
- A `_storage/` package if the iteration-workspace machinery moves
  out of `cli.py` / `workspace.py`.

The rule does NOT apply to:

- Pure file moves where there is no shim (rename-only refactors).
  All patches and imports are updated in lockstep with the move; no
  back-compat surface to defend.
- Cosmetic re-organizations where every caller is updated in the
  same change. Same reasoning — no back-compat illusion to manage.
- New code with no prior callers. There's no shim to ship.
