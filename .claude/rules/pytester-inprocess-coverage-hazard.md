# Rule: Don't patch imported modules inside `pytester.runpytest_inprocess` under coverage

Using `pytester.runpytest_inprocess` inside a `pytest --cov=clauditor`
run is **safe by itself** — the canonical
`tests/test_pytest_plugin.py::TestClauditorSpecInputFiles::test_input_files_copied_to_cwd`
uses it successfully. But once the inner test starts calling
`unittest.mock.patch("some.module.name", ...)` on a target that the
outer coverage session has already instrumented, the combination
triggers intermittent segfaults in **unrelated** test files
(argparse init, random mock entrypoints, dynamic imports from
`pkgutil.resolve_name`). The crashes are order-dependent, hard to
reproduce, and do not mention the pytester test in their traceback.

## The trap

```python
def test_clauditor_blind_compare_via_pytester_injection(self, pytester):
    # ... write a fake skill + eval spec file ...
    pytester.makepyfile("""
        from unittest.mock import AsyncMock, patch

        def test_uses_fixture(clauditor_blind_compare):
            # DANGEROUS: patching a module the outer --cov session is
            # already instrumenting.
            with patch(
                "clauditor.quality_grader.blind_compare",
                new=AsyncMock(return_value=canned),
            ):
                result = clauditor_blind_compare(skill, "a", "b")
            assert result.preference == "a"
    """)
    # Launches a second pytest session inside the already-coverage-hooked
    # outer session. The inner `patch(...)` above corrupts module-cache
    # or argparse state in a way that surfaces as a segfault later, in
    # a completely different test file.
    result = pytester.runpytest_inprocess("-v")
    result.assert_outcomes(passed=1)
```

The symptoms that tell you this is what's happening:

- `pytest` (no `--cov`) passes cleanly.
- `pytest --cov=<pkg>` passes cleanly after **deselecting** the
  pytester test.
- `pytest --cov=<pkg>` with the pytester test present segfaults in
  an **unrelated** test file. The crash site moves between runs.
- Tracebacks mention `argparse._get_optional_kwargs`,
  `unittest/mock.py:__enter__`, or
  `pkgutil.resolve_name`/`importlib.import_module`.

## Safe alternatives

If you need end-to-end fixture-wiring coverage, pick one:

1. **Inner test that does NOT patch anything** — just verify the
   fixture is injected and callable:
   ```python
   pytester.makepyfile("""
       def test_fixture_is_callable(clauditor_blind_compare):
           assert callable(clauditor_blind_compare)
   """)
   ```
   Combined with a direct `__wrapped__` call test for the factory
   body, you get both "the decoration works" and "the body works"
   coverage without the coverage-session hazard.

2. **`pytester.runpytest` (subprocess mode)** — spawns a fresh Python
   process that does NOT inherit the outer coverage hooks. The inner
   test can freely `mock.patch` anything. Trade-off: slower (~1-2s
   per test instead of milliseconds) and can't share in-memory
   fixtures, but segfault-proof.

3. **Trust `__wrapped__` direct calls** — for simple fixture
   factories, calling `my_fixture.__wrapped__(request, ...)` with a
   `MagicMock` request covers the factory body. It does NOT cover
   the `@pytest.fixture` decoration or request wiring, but for
   low-risk fixtures that gap is acceptable.

## The safe-vs-unsafe distinguishing factor

**The mock.patch call is the trigger, not `runpytest_inprocess`
itself.**
`TestClauditorSpecInputFiles::test_input_files_copied_to_cwd` uses
`runpytest_inprocess` without any inner patching and runs cleanly
under `--cov`. Do not read this rule as "never use
`runpytest_inprocess`" — read it as "never combine
`runpytest_inprocess` + `--cov` + `mock.patch` on an
already-imported module."

## Canonical implementation

- The hazard was discovered and removed in commit `925fa63`
  (`clauditor-5x5.4: Remove pytester integration test — coverage
  segfault`) after being introduced in commit `dc96c17`
  (`clauditor-5x5.4: Quality gate — fix findings from code review +
  CodeRabbit`).
- Safe reference use of `runpytest_inprocess` without inner
  patching:
  `tests/test_pytest_plugin.py::TestClauditorSpecInputFiles::test_input_files_copied_to_cwd`.

## When this rule applies

Any time you're tempted to write a pytester-based integration test
that patches `clauditor.*` (or any module the outer `--cov=clauditor`
run is tracking). If your inner test needs a mock, use subprocess
mode or drop back to `__wrapped__` direct calls.
