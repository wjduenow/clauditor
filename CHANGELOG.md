# Changelog

All notable changes to clauditor are tracked here. Pre-1.0 releases may
contain breaking changes without a deprecation shim; see the
**Breaking changes** sections below for migration guidance.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Breaking changes

- **Assertion dicts use per-type semantic keys (#67).** The single
  overloaded `value` key on each `assertions[]` entry has been
  replaced with per-type keys: `needle` (on `contains` /
  `not_contains`), `pattern` (on `regex` / `min_count`), `length`
  (on `min_length` / `max_length`), `count` (on `min_count` /
  `has_urls` / `has_entries` / `urls_reachable` / `has_format`),
  and `format` (on `has_format`). Integer fields are native JSON
  ints, not strings — `{"length": 500}`, not `{"length": "500"}`.
  The loader rejects the old shape at load time with a per-type
  "did you mean?" hint pointing at the correct key. No back-compat
  window ships — hand-edit old specs to the new shape, or regenerate
  them with `clauditor propose-eval --force`. See
  [`docs/eval-spec-reference.md#assertion-types-and-per-type-keys`](docs/eval-spec-reference.md#assertion-types-and-per-type-keys)
  for the full per-type key table.

- **`SkillResult.assert_*` methods moved to `SkillAsserter`.** The
  `assert_contains` / `assert_not_contains` / `assert_matches` /
  `assert_has_urls` / `assert_has_entries` / `assert_min_count` /
  `assert_min_length` / `run_assertions` helpers previously lived
  directly on `SkillResult`. They now live on a separate
  `SkillAsserter` class (`src/clauditor/asserters.py`) to preserve the
  data/asserter split per `.claude/rules/data-vs-asserter-split.md`.
  No deprecation shim ships — pre-1.0 accepts the hard break.

  **Migration:** update tests that use the pytest plugin's
  `clauditor_runner` fixture to wrap the result through
  `clauditor_asserter`:

  ```python
  # Before
  def test_my_skill(clauditor_runner):
      result = clauditor_runner.run("my-skill", args="...")
      result.assert_contains("Results")

  # After
  def test_my_skill(clauditor_runner, clauditor_asserter):
      result = clauditor_runner.run("my-skill", args="...")
      clauditor_asserter(result).assert_contains("Results")
  ```

  See [`docs/pytest-plugin.md`](docs/pytest-plugin.md) for the full
  fixture list and assertion-method reference.

### Added

- **Runner error surfacing (#63).** `SkillResult` gained an
  `error_category` field —
  `"rate_limit" | "auth" | "api" | "interactive" | "subprocess" |
  "timeout" | None` — that classifies any non-clean signal alongside
  the existing `error` string. CLI error rendering now surfaces
  stream-json `is_error: true` result messages with the correct
  category hint, and an interactive-hang heuristic flags runs that
  stop after one turn with a trailing `?` or `AskUserQuestion`
  tool call. The heuristic can be disabled per-spec via
  `allow_hang_heuristic: false`. A new `succeeded_cleanly`
  predicate distinguishes "actually completed cleanly" from the
  lenient `succeeded` flag. See
  [`docs/pytest-plugin.md`](docs/pytest-plugin.md) and
  [`docs/stream-json-schema.md`](docs/stream-json-schema.md).
- **Modern `<name>/SKILL.md` skill layout (#66).** Skill discovery
  now supports both the legacy `.claude/commands/<name>.md` layout
  and the modern `.claude/skills/<name>/SKILL.md` directory layout
  used by Anthropic's plugin / agentskills.io ecosystem. Skill
  identity is derived from YAML frontmatter `name:` first, falling
  back to a layout-aware filesystem derivation (directory name for
  modern layout, file stem for legacy). Invalid or mismatched
  names emit a stderr warning and fall through rather than
  hard-failing. See `.claude/rules/skill-identity-from-frontmatter.md`.
- **Blind A/B judge framing via `user_prompt`.** `EvalSpec` gained
  an optional top-level `user_prompt: str | None` field that feeds
  the conversational framing into `blind_compare_from_spec` and the
  `clauditor_blind_compare` pytest fixture. Distinct from
  `test_args` (which is the CLI arg string for the skill
  subprocess). See
  [`docs/eval-spec-reference.md#optional-top-level-fields`](docs/eval-spec-reference.md#optional-top-level-fields).

### Added (prior unreleased)

- `clauditor propose-eval <SKILL.md>` — LLM-assisted EvalSpec
  bootstrap. Reads SKILL.md and an optional captured run, asks
  Sonnet to propose a full 3-layer EvalSpec (L1 assertions, L2
  tiered extraction, L3 rubric), validates the proposal through
  `EvalSpec.from_dict`, and writes the sibling `<skill>.eval.json`
  (the same discovery path `SkillSpec.from_file` and
  `clauditor init` use, so `validate` / `grade` auto-discover it).
  Captures are scrubbed through `transcripts.redact` (DEC-008) and
  the sidecar preserves the non-mutating-scrub invariant. See
  `docs/cli-reference.md#propose-eval` for flags and exit codes.
- Privacy: `SuggestReport.to_json()` scrubs `api_error` through
  `transcripts.redact()` before emitting so secret-shaped substrings
  (Anthropic keys, GitHub PATs, Bearer tokens) are redacted on disk.
  In-memory `self.api_error` is unchanged (non-mutating scrub per
  `.claude/rules/non-mutating-scrub.md`).
- `cmd_triggers` now exits 1 with `ERROR: No trigger_tests defined in
  eval spec` when the spec is missing `trigger_tests` (previously
  printed an empty `Trigger Precision:` block and exited 0 — a
  CI-silent-failure hazard).
- Root README restructured: deep-reference content promoted into
  `docs/*.md` files with teasers + anchor preservation. README is now
  ~165 lines (was 770). See
  [`.claude/rules/readme-promotion-recipe.md`](.claude/rules/readme-promotion-recipe.md)
  for the codified recipe.
- Bundled `/clauditor` Claude Code slash command installable via
  `clauditor setup`.
