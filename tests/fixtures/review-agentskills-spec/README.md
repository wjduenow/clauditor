# review-agentskills-spec fixtures

Fixture input for `tests/test_bundled_review_skill.py`.

## `captured-output.txt`

Representative output for the `/review-agentskills-spec` skill. The
replay test (`TestRealWorldClauditorExample`) feeds this through
`run_assertions` to verify the Layer 1 assertion spec holds against a
realistic payload — deterministic, free, no API call.

### Current provenance

**Hand-authored representative capture** (2026-04-20). It matches the
shape the skill's workflow prescribes but was not produced by a live
`claude -p` run.

### Refreshing from a real capture

When the skill's workflow or Claude's behavior drifts, regenerate this
file from a live run:

```bash
# Requires ANTHROPIC_API_KEY and the claude CLI installed.
uv run clauditor capture \
  src/clauditor/skills/review-agentskills-spec/SKILL.md

# clauditor capture writes the transcript under
# .clauditor/captures/<skill>.txt — copy it over this fixture:
cp .clauditor/captures/review-agentskills-spec.txt \
   tests/fixtures/review-agentskills-spec/captured-output.txt
```

After refreshing, re-run `pytest tests/test_bundled_review_skill.py`
and confirm the replay assertions still pass. If they don't, either
tighten the eval spec to match the new output shape or treat the
divergence as a real regression.
