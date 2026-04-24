# Example: two-layer dogfood test for a Claude Code skill

A worked reference for testing a Claude Code skill in CI: a captured
replay (always-on, deterministic, free) paired with a gated live run
(opt-in canary, spends tokens). The maintainer-only
`review-agentskills-spec` skill (lives at repo-root `.claude/skills/`,
not shipped in the installed wheel) is the example subject.

> **Internal-only skill.** `review-agentskills-spec` is a maintainer
> tool (it audits the upstream agentskills.io spec against clauditor's
> own implementation). It is **not** installed by `clauditor setup`
> and is not user-facing. The value here is the **test pattern**, not
> the skill itself.

## Files

| Path | Purpose |
| --- | --- |
| `.claude/skills/review-agentskills-spec/SKILL.md` | The skill (frontmatter + workflow). |
| `.claude/skills/review-agentskills-spec/assets/review-agentskills-spec.eval.json` | Sibling eval spec: 5 L1 assertions + 3 L3 grading criteria. |
| `tests/fixtures/review-agentskills-spec/captured-output.txt` | Captured representative skill output used by the replay test. |
| `tests/fixtures/review-agentskills-spec/README.md` | Fixture provenance + refresh protocol. |
| `tests/test_bundled_review_skill.py` | 16 tests across three layers (frontmatter contract, replay, live run). |

## Test layers

### 1. Skill contract (always-on)

Frontmatter shape, `SkillSpec.from_file` / `EvalSpec.from_file` loader
round-trips, stable-id uniqueness. Pins the skill's loader surface.

### 2. Replay (always-on, deterministic)

`TestRealWorldClauditorExample` loads the captured fixture via
`CAPTURED_OUTPUT.read_text()` and runs the declared L1 assertions
against it with `run_assertions`. A companion negative-case test feeds
an empty string through the same pipeline and asserts ≥1 failure —
proof that the spec actually discriminates.

```python
def test_replay_passes_all_l1_assertions(self) -> None:
    spec = EvalSpec.from_file(EVAL_JSON)
    output = _load_captured_output()
    result = run_assertions(output, spec.assertions)
    assert not [r for r in result.results if not r.passed]
```

Runs in milliseconds. No subprocess, no API call, no network.

### 3. Live run (gated, opt-in canary)

`TestLiveSkillRun` invokes `SkillRunner` against the real skill and
runs the same L1 assertions on Claude's actual output. Gated by a
**triple lock** — all three must be set, or the test skips cleanly:

- `CLAUDITOR_RUN_LIVE=1` (explicit opt-in; never implicit)
- `ANTHROPIC_API_KEY` set
- `claude` CLI available on `PATH`

Also tagged `@pytest.mark.live` so it can be selected with `-m live`
or deselected with `-m 'not live'`.

```bash
# Default CI run — live class is skipped.
uv run pytest tests/test_bundled_review_skill.py

# Opt-in canary run — spends tokens.
CLAUDITOR_RUN_LIVE=1 uv run pytest tests/test_bundled_review_skill.py -v

# Run ONLY the live tests (still needs the env var).
CLAUDITOR_RUN_LIVE=1 uv run pytest -m live
```

## Why the layering earns its keep

- **Replay** catches regressions in the L1 pipeline the moment they
  land — `run_assertions`, assertion handlers, the eval-spec loader,
  stable-id uniqueness. Deterministic and free, so it runs on every
  PR.
- **Live** catches regressions in the *skill's actual behavior* —
  Claude's wording drifts, WebFetch returns unexpected content,
  network policy changes. Flaky and expensive, so it runs on a
  schedule (nightly / weekly canary), never by accident.
- **Triple-lock gate** makes it impossible to spend tokens silently.
  Forgetting to set `CLAUDITOR_RUN_LIVE=1` does not fall through to a
  live run; it falls through to a skip.

## Refreshing the replay fixture

When the skill's workflow changes or Claude's output drifts, regenerate
the fixture from a live capture:

```bash
# Requires ANTHROPIC_API_KEY and the claude CLI installed.
uv run clauditor capture review-agentskills-spec

cp .clauditor/captures/review-agentskills-spec.txt \
   tests/fixtures/review-agentskills-spec/captured-output.txt
```

Then re-run `uv run pytest tests/test_bundled_review_skill.py` and
confirm the replay still passes. If not, either tighten the eval spec
to match the new shape or treat the divergence as a real regression.

## Adapting this for your own skill

1. Write an eval spec with Layer 1 assertions capturing the output
   shape you care about (`contains`, `regex`, `min_length`,
   `has_entries`, etc.).
2. Capture one realistic skill output — either from `clauditor capture`
   or hand-authored while iterating. Store it under
   `tests/fixtures/<skill>/captured-output.txt` with a README recording
   provenance + refresh instructions.
3. Write a replay test that loads the fixture + eval spec and runs
   `run_assertions`. Add a negative-case counterpart.
4. Add a live-run test class that triple-locks
   (`CLAUDITOR_RUN_LIVE=1` + env var + CLI availability) and tag it
   `@pytest.mark.live`. Default CI skips it; a nightly workflow opts
   in by exporting the env var.
5. Register the `live` marker in your `pyproject.toml`
   (`[tool.pytest.ini_options].markers`) so pytest does not emit
   "unknown marker" warnings.

See `examples/.claude/skills/find-kid-activities/` for a runnable,
agentskills.io-conformant example: a `SKILL.md` (frontmatter +
workflow), a sibling `SKILL.eval.json` exercising L1 assertions, L2
section extraction, L3 grading criteria, trigger tests, and a
variance budget, plus a realistic `assets/sample-input.txt` referenced
via `input_files`. Verify it conforms with:

```bash
uv run clauditor lint examples/.claude/skills/find-kid-activities/SKILL.md
```
