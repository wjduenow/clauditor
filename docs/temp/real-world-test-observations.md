# Real-World Testing Observations: Clauditor vs. my_claude_agent

**Date:** 2026-04-12
**Test target:** `/home/wesd/Projects/my_claude_agent` — a Twilio/Claude/Gemini voice and messaging agent with 3 existing clauditor eval specs
**Clauditor version:** feature/13-field-validation (after merge to dev) — includes format registry, `urls_reachable`, `has_format`, field pattern/format enforcement

## Methodology

Ran clauditor against real captured `/find-restaurants` output from my_claude_agent. Wrote 13 new tests exercising formats, `has_format`, `urls_reachable`, and `FieldRequirement.format` enforcement. All 24 eval tests pass including real Haiku extraction (Layer 2) and Sonnet grading (Layer 3).

**Files written:**
- `tests/eval/test_new_features.py` (8 tests)
- `tests/eval/test_eval_json_upgrade.py` (4 tests)

**Test run:** `24 passed, 1 warning in 27.27s`

---

## High-value observations (product / UX)

### 1. `url` format is too strict — it requires `https://` scheme but LLMs return bare domains

Haiku extracted `website: "paesanosj.com"` from a markdown link like `[paesanosj.com](https://paesanosj.com/)`. The LLM picked the display text, not the href. Our `format="url"` then rejects *every single restaurant* as invalid even though the data is correct.

**Evidence:**
```
[FAIL] section:Restaurants/default[0].website:format: Value does not match format 'url'
  evidence: paesanosj.com
```

**Fix:** Add a `domain` format (`[a-z0-9-]+(\.[a-z0-9-]+)+`), and/or make extraction smarter about markdown links, and/or document this pitfall.

---

### 2. Layer 2 output is overwhelming for failures — 33 lines of assertion results for 6 entries × 4 fields

When the Haiku grader runs, a single failed format check produces output like:
```
[FAIL] section:Restaurants/default[0].website:format: Value does not match format 'url'
[FAIL] section:Restaurants/default[1].website:format: Value does not match format 'url'
[FAIL] section:Restaurants/default[2].website:format: Value does not match format 'url'
```

Across 6 entries, that's 6 nearly identical failures. There's no aggregation (`"6/6 websites failed url format"`), no deduplication of failure modes.

**Fix:** Group assertions by field and failure reason in `AssertionSet.summary()`. Add a `grouped_summary()` method that collapses repeated failures.

---

### 3. There's no way to capture skill output as part of the test workflow

The eval tests all depend on `tests/eval/captured/find-restaurants.txt` — a blob someone manually ran and pasted. `find-events` and `find-kid-activities` have eval specs but **no capture**, so they're untestable. There's no CLI command like `clauditor capture /find-restaurants --args "..."` that runs the skill and saves output.

**Fix:** `clauditor capture <skill> [args]` subcommand that shells out to `claude -p "/skill args"` and writes to `tests/eval/captured/<skill>.txt`. Could also auto-version captures (`find-restaurants-2026-04-12.txt`).

---

### 4. No `pytest.mark.slow` or `pytest.mark.network` registration

My `urls_reachable` test triggered a `PytestUnknownMarkWarning`. Clauditor should register a `network` or `slow` marker automatically via its pytest plugin, so downstream projects get clean behavior out of the box.

**Evidence:**
```
PytestUnknownMarkWarning: Unknown pytest.mark.slow - is this a typo?
```

**Fix:** Add `config.addinivalue_line("markers", "network: real HTTP; deselect with -m 'not network'")` in `pytest_plugin.py`.

---

### 5. Zero-cost A/B comparison is missing a "baseline" story

`comparator.py` exists but I couldn't find a workflow for "compare my new skill version vs. the last passing one." A real eval loop needs: run skill twice, diff assertion pass rates, show deltas. This is what eval suites are for — catching regressions.

**Fix:** `clauditor compare <capture_before> <capture_after> --spec x.eval.json` with rich diff output showing which assertions flipped.

---

## Developer ergonomics

### 6. Installation collision — `uv run` chose Python 3.10, venv was 3.11, clauditor requires 3.11

When I tried `uv run python -c "..."` it silently used a different Python than the project venv. Clauditor's editable install in a `path = "../clauditor"` pyproject entry wasn't editable by default — changes to clauditor source weren't picked up. I had to manually copy files. Most users hitting this will just give up.

**Evidence:** I had to run:
```bash
cp /home/wesd/Projects/clauditor/src/clauditor/*.py \
   /home/wesd/Projects/my_claude_agent/.venv/lib/python3.11/site-packages/clauditor/
```

**Fix:** Document the `editable = true` requirement prominently in README; add a `clauditor doctor` command that detects version/install mismatches and surfaces Python version mismatches.

---

### 7. `FieldRequirement.pattern` vs `FieldRequirement.format` is confusing — no docs on when to use which

I shipped both. Looking at my test file a week from now, I won't remember if `format="phone_us"` is a shortcut for `pattern=r"\(\d{3}\)..."` or if they're different. The answer (named registry entry vs. inline regex) is in the code comments but nowhere visible to users.

**Fix:** README section with decision tree; `clauditor formats list` CLI command that shows the pattern behind each format name. Consider deprecating `pattern` since `format` is more maintainable.

---

### 8. Field presence failures and format failures both use `:field` suffix — hard to filter

Failure names are `section:Restaurants/default[0].website` (missing) vs `section:Restaurants/default[0].website:format` (wrong format). To filter "just show me missing fields" I'd need to exclude `:format` and `:pattern`. There's no structured type.

**Fix:** Add `kind` field to `AssertionResult`: `"presence" | "pattern" | "format" | "count" | "custom"`. Enables programmatic filtering and grouping.

---

### 9. `urls_reachable` has no observability into partial failures across runs

When `urls_reachable=3` succeeds with 3/3, we log it. When `has_format=3` succeeds with 5/5, we log it. But if I want to know "the output changed from 3 phone matches to 5 phone matches" — that's a non-regression signal about scope drift — there's nowhere to record/compare numeric metrics across runs.

**Fix:** Persist assertion metrics to a time-series log (`.clauditor/history.jsonl`), expose via `clauditor trend <skill> --metric has_format:phone_us`.

---

### 10. Layer 2 grader silently inflates expected entries from LLM hallucination

The Haiku grader returned 6 restaurants from an output that visibly has 3 numbered entries. It extracted "Original Joe's", "Palermo Italian", "Maggiano's" from narrative mentions in the prose (they're discussed as "also worth noting" in source citations). The eval spec requires `min_entries: 3`, so this passes — but it's actually a *precision failure*, not a *recall success*.

**Evidence:** Only `### 1.`, `### 2.`, `### 3.` in the source, but extraction returned 6 entries.

**Fix:** Distinguish "primary entries" from "mentioned entities" in the extraction prompt, or add a `max_entries` field to `TierRequirement` so we catch runaway extraction. Current spec says "at least 3 restaurants" when the intent is "exactly 3 top picks."

---

## Beyond 10 — structural

### 11. No shared conftest or fixture factory for eval tests

Every test class re-creates `output` and `eval_spec` fixtures. Clauditor's pytest plugin exposes `clauditor_runner`/`clauditor_spec` but there's no `clauditor_captured_output("find-restaurants")` helper.

**Fix:** Add `@clauditor_capture("skill-name")` decorator / fixture that auto-loads capture + spec, with a known directory convention.

---

### 12. Assertion messages use Unicode `≥` that doesn't grep well

`has_urls≥3`, `urls_reachable≥1`. If I want to grep test logs for "has_urls" I'll find it fine, but `grep "≥"` requires UTF-8 literal. Minor but annoying in CI logs and terminal output for users on non-UTF-8 locales.

**Fix:** Use `>=` in ASCII form or add a `--ascii` mode to `AssertionSet.summary()`.

---

### 13. No way to scope Layer 3 quality grading by criterion — all or nothing

The quality grader runs all `grading_criteria` at once and charges Sonnet for each. On a flaky test run I'd want to re-run just one criterion. No such option exists.

**Fix:** Add `--only-criterion <name>` to the quality_grader API and a `criteria` parameter to `grade_quality()`.

---

### 14. Coverage of the format registry is untested against adversarial inputs

`phone_us` pattern `\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}` matches `1234567890` and `(408)2985437` and `408.298.5437` — great for extraction, terrible for validation. A strict validator should distinguish "formatted" from "digit soup." There's no way to express "strict" vs "loose" mode for a format.

**Fix:** Each `FormatDef` could have `strict_pattern` (for fullmatch) and `extract_pattern` (for findall) — the stub is there but currently they're the same for most entries. Populate `strict_pattern` for every format.

---

### 15. `extract_and_grade` failures don't surface the raw Haiku JSON

When the grader fails validation, the `AssertionResult.evidence` shows individual field values, but the original Haiku JSON blob is only stored on `ExtractedOutput.raw_json` and never surfaced through `AssertionSet`. Debugging "why did Haiku think there were 6 restaurants?" requires dropping into a debugger.

**Fix:** Attach the raw JSON as a special `grader:raw` assertion evidence or write it to `.clauditor/last_grader_response.json` automatically.

---

## Priority ranking for follow-up

| # | Priority | Effort | Impact |
|---|----------|--------|--------|
| 3 | **P0** | medium | High — unblocks testing for skills without manual captures |
| 1 | **P0** | low | High — `url` format failing on valid data is a trap for new users |
| 10 | **P1** | medium | High — silent precision failures undermine trust in Layer 2 |
| 2 | P1 | low | Medium — failure output noise hurts debugging UX |
| 4 | P1 | very low | Low — one-line fix, eliminates warning |
| 6 | P1 | low | Medium — first-run UX is often broken |
| 5 | P2 | medium | Medium — enables eval-driven regression workflow |
| 8 | P2 | low | Low — enables programmatic filtering |
| 7 | P2 | low | Low — docs-only improvement |
| 14 | P2 | medium | Medium — strict validation is the whole point of format |
| 9 | P3 | high | Medium — trend tracking is a full feature |
| 15 | P3 | low | Low — debuggability improvement |
| 11 | P3 | low | Low — quality-of-life for users with multiple skills |
| 12 | P3 | very low | Low — cosmetic |
| 13 | P3 | medium | Low — cost optimization for heavy users |

---

## Bottom line

Clauditor is **usable today** — I wrote 4 new test classes in ~10 minutes and they all passed.

The biggest real-world gap is the **capture workflow** (#3) — without it, only skills with manual captures can be tested, which is exactly why `find-events` and `find-kid-activities` have evals but no coverage.

**#1** (bare-domain vs URL) and **#10** (over-extraction) are both traps that would bite anyone upgrading from string-match evals to structured evals. Both should be fixed before this is promoted as "the way" to test Claude Code skills.
