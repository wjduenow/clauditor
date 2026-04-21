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

- **`clauditor lint` — agentskills.io spec conformance check (#71).** A
  non-LLM static check that validates a `SKILL.md` file against the
  [agentskills.io specification](https://agentskills.io/specification).
  Safe to run on every commit and in CI — no tokens spent, no network
  calls.
  - Positional path argument; resolves absolute paths, follows
    symlinks, rejects directories with exit 1.
  - `--strict` promotes warnings to exit 2 (pre-publish gate).
    Load-layer parse failures (`AGENTSKILLS_FRONTMATTER_INVALID_YAML`,
    unreadable path) always exit 1 regardless of `--strict`.
  - `--json` emits a single JSON envelope to stdout
    (`{"schema_version": 1, "skill_path": ..., "passed": bool,
    "issues": [...]}`) with identical exit codes to the human-text
    output path.
  - Pure compute lives in `src/clauditor/conformance.py` per
    `.claude/rules/pure-compute-vs-io-split.md` — `check_conformance`
    returns a `list[ConformanceIssue]` with stable `code` / `severity`
    / `message` fields, testable without `tmp_path` or `capsys`.
  - **Soft-warn hook on `SkillSpec.from_file`**: every skill load now
    runs `check_conformance` and emits warning-severity issues to
    stderr with the `clauditor.conformance: <CODE>: <message>` prefix.
    Errors are silent at this seam — they surface through
    `clauditor lint`. The hook never blocks spec loading.
  - **`KNOWN_CLAUDE_CODE_EXTENSION_KEYS` allowlist** for frontmatter
    keys that Claude Code uses but the agentskills.io spec does not
    define. Initial contents: `argument-hint`,
    `disable-model-invocation`. Keys in the allowlist do NOT trigger
    `AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY`. The bundled
    `/review-agentskills-spec` skill maintains the allowlist against
    Claude Code's published frontmatter documentation (DEC-013).
  - **`paths.derive_skill_name` warning emission retired**: the
    previous warnings for invalid frontmatter-name fallback and
    frontmatter-vs-filesystem mismatch are now produced by
    `check_conformance` via the soft-warn hook — single source of
    truth for frontmatter-name warnings. The helper now always returns
    `(name, None)`. See
    [`docs/cli-reference.md#lint`](docs/cli-reference.md#lint) and
    `.claude/rules/skill-identity-from-frontmatter.md`.
- **Runner auth-source control + configurable timeout (#64).** Four
  skill-invoking CLI commands (`validate`, `grade`, `capture`, `run`)
  and the pytest plugin gained two knobs that together unblock Pro/Max
  subscribers iterating on research-heavy skills:
  - `--no-api-key` / `--clauditor-no-api-key` (pytest) strip both
    `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the `claude -p`
    subprocess environment so the child falls back to whatever auth is
    cached in `~/.claude/` (typically a Pro/Max subscription with a
    much higher throughput ceiling than the API-key tier). Non-auth
    Anthropic env vars such as `ANTHROPIC_BASE_URL` are preserved.
  - `--timeout SECONDS` overrides the runner's 180-second watchdog on
    a per-invocation basis. Must be a positive integer; argparse
    rejects `<= 0` / non-int with exit 2. Precedence is
    **CLI > spec > default**: the flag wins when passed explicitly,
    otherwise a new `EvalSpec.timeout` field wins when set, otherwise
    the built-in 180s default applies. `EvalSpec.timeout` is
    load-time validated (positive int only; `bool` is explicitly
    rejected because it is an `int` subclass in Python).
  - `SkillResult.api_key_source` carries the `apiKeySource` value
    parsed from the stream-json `system/init` event (when present);
    the runner prints one stderr info line of the form
    `clauditor.runner: apiKeySource=<value>` per run. Values are
    labels (`"ANTHROPIC_API_KEY"`, `"claude.ai"`, `"none"`), not
    secrets. Older `claude` builds that omit the field leave
    `api_key_source` at `None` and suppress the stderr line. See
    [`docs/cli-reference.md#shared-runner-flags-validate-grade-capture-run`](docs/cli-reference.md#shared-runner-flags-validate-grade-capture-run),
    [`docs/eval-spec-reference.md#optional-top-level-fields`](docs/eval-spec-reference.md#optional-top-level-fields),
    and [`docs/stream-json-schema.md`](docs/stream-json-schema.md).
    Precedence shape codified in
    [`.claude/rules/spec-cli-precedence.md`](.claude/rules/spec-cli-precedence.md).
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
