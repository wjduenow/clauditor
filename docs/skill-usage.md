# Using /clauditor in Claude Code

Walkthrough of the bundled `/clauditor` slash command: what it does when invoked inside Claude Code, how it differs from the CLI entry points, and when to reach for each. Read this when you want conversational evaluation inside a Claude session; reach for the CLI reference when you're scripting CI.

> Returning from the [root README](../README.md). This doc is the full reference; the README has a summary with code examples.

Once `clauditor setup` has installed the symlink, the bundled skill is
available as a slash command in any Claude Code session rooted at this
project. The command is manual-only — Claude won't auto-invoke it,
because validating a skill has side effects (subprocess runs, sidecar
writes, potential token spend on L3 grading).

clauditor works with both the legacy `.claude/commands/<name>.md` layout and the modern `.claude/skills/<name>/SKILL.md` layout.

**Invoke with the path to the skill you want to evaluate:**

```text
/clauditor .claude/commands/my-skill.md
```

or, for a directory-layout skill:

```text
/clauditor .claude/skills/my-skill/SKILL.md
```

Running `/clauditor` without an argument prompts Claude to ask which
skill to evaluate.

**What Claude does:**

1. Locates the skill's eval spec — the sibling `.eval.json` auto-discovered
   by `skill_path.with_suffix('.eval.json')` (e.g. `SKILL.md` →
   `SKILL.eval.json`). Other locations (for example
   `<skill-dir>/assets/<skill-name>.eval.json`) require passing `--eval <path>`
   explicitly.
2. If no spec exists, bootstraps one via `clauditor propose-eval` —
   an LLM-assisted bootstrap that writes `<skill_stem>.eval.json` next to
   the file you pass. Claude starts with `--dry-run` for a cost-free
   preview of the proposer prompt, reviews it with you, then drops the
   flag to write the spec. See
   [`docs/cli-reference.md#propose-eval`](cli-reference.md#propose-eval)
   for the full flag reference.
3. Runs L1 validation first (`clauditor validate`) — free, sub-second,
   reports failing assertion ids.
4. If L1 passes, asks before running L3 grading (`clauditor grade`) —
   costs Sonnet tokens, writes a full `grading.json` sidecar.
5. If L3 reports failing criteria, asks before running
   `clauditor suggest` — proposes a unified diff of SKILL.md edits
   motivated by the failing criterion ids. Writes
   `<skill-name>-<timestamp>.diff` and `<skill-name>-<timestamp>.json`
   under `.clauditor/suggestions/` (`<skill-name>` is the skill's
   derived identity — frontmatter `name:` or parent-directory name,
   not the file stem). Shows you the diff plus `motivated_by` +
   `confidence` from the sidecar; does not auto-apply — review,
   `git apply` (or hand-edit), then re-run `validate` / `grade` to
   measure the score delta.
6. Summarizes: which layers ran, pass/fail counts, sidecar paths
   (including the suggest diff path if it ran) you can open for
   details.

**When to use `/clauditor` vs. the CLI directly:**

- Use `/clauditor` when you're in a Claude Code conversation and want
  conversational context (Claude can explain failures, suggest fixes,
  iterate on the spec).
- Use `clauditor validate` / `clauditor grade` directly in CI,
  Makefiles, or scripted workflows where you want deterministic exit
  codes and no LLM narration.

The full skill playbook lives at
[`src/clauditor/skills/clauditor/SKILL.md`](../src/clauditor/skills/clauditor/SKILL.md)
(what Claude reads when the slash command fires).

## Proposing skill improvements

When `clauditor grade` returns failing L3 criteria, you have a
grading.json that names exactly which criteria missed and why.
`clauditor suggest` closes the loop: it reads that grading.json,
asks Sonnet to propose minimal SKILL.md edits keyed to the failing
criterion ids, and writes a unified diff plus a JSON sidecar so you
can review before anything lands in your SKILL.md.

**The loop:**

```bash
clauditor grade    .claude/skills/my-skill/SKILL.md   # 1. produces grading.json (L3 failures)
clauditor suggest  .claude/skills/my-skill/SKILL.md   # 2. proposes SKILL.md edits
# review the diff…
git apply .clauditor/suggestions/my-skill-<timestamp>.diff   # 3. apply (or hand-edit)
clauditor grade    .claude/skills/my-skill/SKILL.md   # 4. re-grade to measure delta
```

The command reads the **latest** iteration that contains a
`<skill>/grading.json`; pass `--from-iteration N` to target an
older run explicitly.

**Sidecar shape (`.clauditor/suggestions/<skill>-<timestamp>.json`):**

```json
{
  "schema_version": 1,
  "skill_name": "my-skill",
  "model": "claude-sonnet-4-6",
  "generated_at": "2026-04-24T18:03:22.451820Z",
  "source_iteration": 7,
  "source_grading_path": ".clauditor/iteration-7/my-skill/grading.json",
  "input_tokens": 4812,
  "output_tokens": 311,
  "duration_seconds": 9.42,
  "summary_rationale": "Three edits address the 'distance_ok' criterion by tightening the distance constraint in the Scope section.",
  "edit_proposals": [
    {
      "id": "edit-1",
      "anchor": "Return the five closest venues.",
      "replacement": "Return up to five venues within the specified radius; prefer closer results and omit any venue beyond the radius.",
      "rationale": "Adds an explicit radius constraint so the skill stops returning out-of-range results.",
      "confidence": 0.82,
      "motivated_by": ["distance_ok"],
      "applies_to_file": "SKILL.md"
    }
  ],
  "validation_errors": [],
  "parse_error": null,
  "api_error": null
}
```

**What each field buys you:**

- `motivated_by` — list of failing criterion / assertion ids that
  drove the edit. Traces every proposed change back to a concrete
  grader signal; if no failing signal exists for an id, the
  proposer is rejected at parse time.
- `anchor` — a verbatim substring of the current SKILL.md that
  must appear **exactly once**, hard-validated before the sidecar
  is written. A too-short anchor that matches multiple places or a
  hallucinated anchor that matches nothing fails the whole run
  with exit 2 — no partial diff, no silent drift.
- `replacement` — the exact text to swap in at the anchor site.
- `confidence` — proposer's self-reported `[0, 1]` confidence.
  Surface low-confidence edits to the user first; do not auto-apply.
- `rationale` / `summary_rationale` — per-edit and run-wide
  explanation. Use them in commit messages.
- `schema_version` — `1`, first key by convention so downstream
  tooling can pin on it without scanning the whole payload.

**Safety rails:**

- Anchor exactly-once invariant — every `edit_proposal.anchor` is
  validated against the on-disk SKILL.md *sequentially* (edits
  apply in order, and a later edit's anchor must still resolve
  uniquely after earlier edits apply). Any failure aborts the run
  before writing either file.
- No auto-apply — the command writes a diff and a sidecar; it
  never mutates your SKILL.md. You review, then `git apply` (or
  hand-edit).
- Traceability — because every edit carries `motivated_by`, you
  can tie a specific SKILL.md change back to the failing grader
  signal that motivated it, weeks or months later.

**Common failure modes:**

- `no iteration under .clauditor/ contains <skill>/grading.json` —
  run `clauditor grade` first.
- `anchor not found in SKILL.md` / `anchor appears N times (must be
  exactly once)` — the proposer hallucinated or picked a non-unique
  anchor. Re-run; the command is idempotent. Persistent failures
  can indicate SKILL.md text drifted between the grade run and the
  suggest run.
- Exit 3 — Anthropic API error (auth, rate limit, 5xx). No sidecar
  written; retry once the upstream issue clears.

Full flag reference: [`cli-reference.md#suggest`](cli-reference.md#suggest).
