# Changelog

All notable changes to clauditor are tracked here. Pre-1.0 releases may
contain breaking changes without a deprecation shim; see the
**Breaking changes** sections below for migration guidance.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **How this is maintained.** Day-to-day work goes under `[Unreleased]`; on
> release, those entries are promoted under a new version header dated with
> the release day. The `/release-manager` skill uses the version section as
> the GitHub Release body via `gh release create --notes-file`, so this file
> is the single source of truth for human-facing release notes.

## [Unreleased]

### Breaking changes

- **Codex auth pre-flight refuses ChatGPT-mode credentials (#177, PR #181).**
  `check_codex_auth` now examines `~/.codex/auth.json` (or
  `$CODEX_HOME/auth.json` when set) and refuses pre-flight when the file
  declares `auth_mode == "chatgpt"`, regardless of whether
  `CODEX_API_KEY` / `OPENAI_API_KEY` is exported. Rationale: the codex
  subprocess reads `auth.json` and routes via ChatGPT in that mode,
  rejecting every model server-side with `"The 'gpt-5-codex' model is
  not supported when using Codex with a ChatGPT account."` The pre-flight
  refusal fails fast with an actionable `CodexAuthMissingError` pointing
  at `codex login --with-api-key`, rather than letting users burn a
  subprocess + API round-trip on a guaranteed-to-fail call.

  Auth.json reads are failure-open per DEC-005 — a missing, unreadable,
  malformed, or oversize (> 1 MB) `auth.json` falls through to the
  existing env-var / PATH-on-disk checks. Parsed content (tokens,
  account ids) is never serialized to sidecars, logs, or error
  messages per DEC-014.

  **Supersedes #175 DECs 001, 002, 003, 004, 008, and 009** (see the
  cross-link table in `plans/super/177-codex-auth-mode-conflict.md`).
  The #175 plan doc stays historical; the refinement trail lives in
  the #177 plan.

  **Migration:** users with `~/.codex/auth.json` in chatgpt-mode should
  run `codex login --with-api-key` to re-materialize the credentials
  file in API-key mode. Users who never logged in via the ChatGPT flow
  are unaffected.

- **`clauditor_runner` is now a factory fixture (#155).** Pytest fixtures
  cannot accept call-site kwargs; only factory fixtures can. To support
  the new `harness=` operator-intent kwarg, `clauditor_runner` was
  converted from a value fixture to a factory.

  **Migration:**

  ```python
  # Before
  def test_my_skill(clauditor_runner):
      runner = clauditor_runner          # value fixture
      result = runner.run("my-skill")

  # After
  def test_my_skill(clauditor_runner):
      runner = clauditor_runner()        # factory — call it
      result = runner.run("my-skill")
  ```

  No deprecation shim ships — pre-1.0 accepts the hard break (same
  precedent as the `SkillResult.assert_*` → `SkillAsserter` migration in
  v0.1.0). See [`docs/pytest-plugin.md#parametrizing-harness--provider`](docs/pytest-plugin.md#parametrizing-harness--provider).

### Added

- **Documentation: three new deep-dive docs.** Filled doc gaps that
  accumulated as the multi-provider / multi-harness / cost-tracking
  surface landed:
  - [`docs/codex-harness.md`](docs/codex-harness.md) — user-facing
    narrative for running skills under the OpenAI Codex CLI (auth,
    four-layer precedence, sandbox modes, troubleshooting, useful
    harness × grader pairings).
  - [`docs/cost-tracking.md`](docs/cost-tracking.md) — the `context.json`
    sidecar shape, `cost_usd` estimation rules, the pricing table,
    reasoning-token semantics, and the always-v1 contract.
  - [`docs/audit-trend-workflow.md`](docs/audit-trend-workflow.md) —
    end-to-end story for the iteration-history surface: how `grade`
    builds history, how `audit` / `trend` / `compare` / `badge`
    consume it, and how cross-axis comparability refusal protects
    against silent averaging across stacks.
  
  README updated to mention the harness axis and cost tracking in
  prose, and the Reference docs list now links all three new docs
  plus the previously-orphaned `codex-stream-schema.md`.

- **Pytest fixtures: `harness` × `provider` parametrization (#155).** The
  pytest plugin gained two new CLI options and four new factory kwargs so
  a single test can sweep across `{claude-code, codex} × {anthropic, openai}`
  without changing skill or eval-spec files.
  - **New pytest CLI options:**
    - `--clauditor-harness {claude-code, codex, auto}` — overrides the
      harness used by `clauditor_runner` session-wide. (Note:
      `clauditor_spec` honors only `EvalSpec.harness` — set `harness:`
      in `eval.json` for per-skill author preference.)
    - `--clauditor-grading-provider {anthropic, openai, auto}` — overrides
      the grading provider used by `clauditor_grader`,
      `clauditor_blind_compare`, and `clauditor_triggers` session-wide.
  - **New factory kwargs (operator-intent, top of precedence stack):**
    - `clauditor_runner(harness=...)` — pin harness for this runner.
    - `clauditor_grader(skill, eval_path, output, *, provider=..., model=...)`
      — both new factory kwargs. (Pre-#155 the fixture only consulted
      the `--clauditor-model` pytest option; the kwarg layer is new.)
    - `clauditor_blind_compare(skill, output_a, output_b, eval_path, *, provider=..., model=...)`
      — both new.
    - `clauditor_triggers(skill, eval_path, *, provider=..., model=...)`
      — both new.
  - **Operator-intent precedence (highest → lowest):** factory kwarg >
    pytest CLI option > env var (`CLAUDITOR_HARNESS`,
    `CLAUDITOR_GRADING_PROVIDER`) > spec field (`EvalSpec.harness`,
    `EvalSpec.grading_provider`) > default `"auto"`. Mirrors the CLI seam
    exactly per `.claude/rules/spec-cli-precedence.md`.
  - **Eager `check_codex_auth` from `clauditor_runner` and `clauditor_spec`.**
    When the resolved harness is `"codex"`, both factories fire
    `check_codex_auth` before returning the runner / wrapped spec; missing
    auth raises `CodexAuthMissingError` (a sibling of `Exception`, NOT a
    subclass of `AnthropicAuthMissingError` / `OpenAIAuthMissingError`)
    so callers route on a structural `except` ladder rather than
    substring-matching error text.
  - **Asymmetry note.** There is intentionally no
    `CLAUDITOR_FIXTURE_ALLOW_OPENAI` env var (DEC-001 of #155). The
    existing `CLAUDITOR_FIXTURE_ALLOW_CLI=1` opts into a *relaxed*
    Anthropic guard (accepts subscription auth via the `claude` CLI on
    PATH); OpenAI has no CLI-fallback / subscription analog (per #145
    DEC-002), so there is nothing to opt into. See
    [`docs/pytest-plugin.md`](docs/pytest-plugin.md) for the full
    documentation including a `pytest.mark.parametrize` worked example.
- **`EvalSpec.system_prompt: str | None = None` field (#150).** Mirrors
  `user_prompt`'s shape and validation: optional at load time, when set
  must be a non-empty, non-whitespace string (`EvalSpec.from_file`
  rejects empty strings, whitespace-only strings, and non-string
  values). When unset, clauditor auto-derives the system prompt from
  the `SKILL.md` body (post-frontmatter, via `parse_frontmatter`) at
  `SkillSpec.run` time. Explicit `EvalSpec.system_prompt` wins over
  the auto-derived `SKILL.md` body. Auto-derive failures (missing
  file, malformed frontmatter) raise a `RuntimeError` naming the
  skill and path. Frontmatter `system_prompt:` keys inside `SKILL.md`
  are NOT supported (DEC-003) — the body is the auto-derive source.
  See [`docs/eval-spec-reference.md#system-prompt`](docs/eval-spec-reference.md#system-prompt).
- **`Harness.build_prompt(skill_name, args, *, system_prompt) -> str`
  protocol method (#150).** Third member of the cross-harness
  `Harness` protocol (alongside `invoke` and `strip_auth_keys`). Each
  harness owns its identity-to-prompt strategy: `ClaudeCodeHarness`
  keeps the slash-command synthesis (`f"/{skill_name} {args}"`, or
  `f"/{skill_name}"` when args is empty) and ignores `system_prompt`
  because the `claude -p` CLI has no separate system-prompt channel;
  `MockHarness` records `(skill_name, args, system_prompt)` on
  `build_prompt_calls` for test assertions and returns a deterministic
  stub that surfaces all three inputs. The forthcoming `CodexHarness`
  (#149) will consume `system_prompt` as the system message. See
  [`docs/architecture.md#3-harness-protocol`](docs/architecture.md#3-harness-protocol).
- **`SkillRunner.run(..., system_prompt=...)` keyword-only kwarg
  (#150).** Threads the resolved `system_prompt` from `SkillSpec.run`
  to the harness's `build_prompt`. Keyword-only and placed last so it
  cannot collide positionally with the existing `cwd` / `env` /
  `timeout` kwargs. `SkillSpec.run` resolves the effective value once
  (explicit `EvalSpec.system_prompt` > auto-derived `SKILL.md` body)
  and threads the resolved string through this kwarg.

### Changed

- **Codex subprocess error mapping: chatgpt-mode rejection classified as
  `"auth"` (#177, PR #181).** When the pre-flight refusal is bypassed
  (e.g. `~/.codex/auth.json` is created / mutated after the pre-flight
  check, or a sandboxed CI environment skips the parser), the codex
  subprocess emits the server-side rejection string
  `"The 'gpt-5-codex' model is not supported when using Codex with a
  ChatGPT account."` `_classify_codex_failure` now classifies this as
  `error_category = "auth"` rather than `"api"`, matching the actual
  failure mode (credentials are pointing at the wrong auth surface).
  No sidecar schema bump per DEC-011 — new `Literal` values inside an
  existing field are additive.

- **Codex CLI-on-PATH announcement body reworded (#177, PR #181).**
  `_CODEX_CLI_ON_PATH_ANNOUNCEMENT` no longer mentions "the ChatGPT-login
  flow"; the post-#177 body describes codex resolving credentials from
  `~/.codex/auth.json` in API-key mode. Three durable substrings remain
  pinned by tests: `"codex"`, `"PATH"`, `"~/.codex/auth.json"`. The
  announcement fires under the narrowed condition that the PATH branch
  is the load-bearing acceptance signal (env vars unset, codex on PATH,
  and `auth.json` is absent or declares `auth_mode != "chatgpt"`); the
  chatgpt-mode refusal raises `CodexAuthMissingError` and does not fire
  the announcement (the exception is the user signal).

## [0.1.1] - 2026-04-26

### Added

- **`AGENTSKILLS_REFERENCE_DEPTH_TOO_DEEP` conformance warning (#129) —
  agentskills.io spec sync.** `clauditor lint` now flags Markdown link / image targets and
  reference-style definitions in a SKILL.md body that point more than
  one directory deep, plus parent-escape (`..`) paths. Matches the
  agentskills.io spec's "one level deep from SKILL.md" guidance.
  Same-directory and one-subdirectory references stay silent; fenced
  code blocks, URL schemes, anchors, and absolute paths are skipped;
  per-target de-dupe so a target referenced N times produces a single
  warning.
- **Bundled `/clauditor` skill — diagnostics discoverability (#134).**
  The SKILL.md "Common errors" section now mentions `clauditor lint`
  and `clauditor doctor`, so users hitting a lint failure or a runner
  misconfiguration find the right next-step command from the
  in-skill text.

### Changed

- **`AGENTSKILLS_LICENSE_EMPTY` message phrasing (#129) — agentskills.io
  spec sync.** Previously suggested a "non-empty SPDX identifier",
  misleading for authors who want free-form license text. Updated to
  "non-empty license name or path to a bundled license file" to match
  the spec wording. No behavior change.
- **Bundled `/clauditor` skill packaging (#134).** The maintainer-only
  `assets/clauditor.eval.json` (the pre-release dogfood gate per
  DEC-007 of #43) no longer ships in the wheel — it remains in the
  repo for in-source dogfood runs only. SKILL.md `docs/cli-reference.md`
  references now point at stable `blob/dev` GitHub URLs so they resolve
  when SKILL.md is rendered outside the repo. `allowed-tools` trimmed
  to `Bash(clauditor *), Bash(uv run clauditor *)` (the redundant
  narrower entries are removed).

## [0.1.0] - 2026-04-25

First stable release on PyPI: <https://pypi.org/project/clauditor-eval/0.1.0/>.

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

- **`clauditor badge` — shields.io endpoint JSON for skill quality (#77).**
  A non-LLM aggregator that reads the latest (or `--from-iteration N`)
  iteration sidecars and produces a shields.io-compatible JSON file
  pasted into a README as a one-line Markdown image. Badge JSON carries
  a top-level `schemaVersion: 1` (shields.io's contract) and a nested
  `clauditor.schema_version: 1` extension block (our internal version,
  per `.claude/rules/json-schema-version.md`).
  - Positional `<skill-path>` loads via `SkillSpec.from_file` (derives
    `skill_name` from frontmatter, per
    `.claude/rules/skill-identity-from-frontmatter.md`).
  - Default output `.clauditor/badges/<skill>.json`; `--output PATH`
    accepts absolute paths, `--force` required to overwrite.
  - `--url-only` prints the Markdown image line to stdout with
    `git remote get-url origin` + default-branch auto-detection;
    `--repo USER/REPO [--branch NAME]` overrides; `USER/REPO/main`
    placeholder with a stderr warning when detection fails.
  - Color logic: `brightgreen` (L1 all-pass + L3 met or absent),
    `yellow` (L1 all-pass + L3 below thresholds), `red` (any L1 fail
    OR L3 all parse-failed), `lightgrey` (no iteration yet OR spec
    declares zero L1 assertions — both write a "no data" placeholder
    and exit 0 so CI pipelines have a persistent badge).
  - `--style KEY=VALUE` (repeatable) passes shields.io fields through:
    `style`, `logoSvg`, `logoColor`, `labelColor`, `cacheSeconds`,
    `link`. Unknown keys warn but still emit (shields.io ignores).
  - Exit codes 0 / 1 / 2 per
    `.claude/rules/llm-cli-exit-code-taxonomy.md` (non-LLM branch):
    0 success, 1 runtime failure (corrupt iteration, collision without
    `--force`, explicit-missing iteration), 2 input validation (mutual
    exclusion, bad `--output` parent, bad `--style`, bad `--label`).
  - Pure compute lives in `src/clauditor/badge.py` per
    `.claude/rules/pure-compute-vs-io-split.md`; CLI I/O lives in
    `src/clauditor/cli/badge.py`; git metadata wrapper in
    `src/clauditor/_git.py`.
  - See [`docs/badges.md`](docs/badges.md) for the full placement
    guide (README primary, catalog-page secondary, SKILL.md body
    tradeoff) and the CI embedding recipe.

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
    `AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY`. The maintainer-only
    `/review-agentskills-spec` skill maintains the allowlist against
    Claude Code's published frontmatter documentation (DEC-013).
  - **`paths.derive_skill_name` warning emission retired**: the
    previous warnings for invalid frontmatter-name fallback and
    frontmatter-vs-filesystem mismatch are now produced by
    `check_conformance` via the soft-warn hook — single source of
    truth for frontmatter-name warnings. The helper's return type
    simplified from `tuple[str, str | None]` to plain `str`. See
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

[Unreleased]: https://github.com/wjduenow/clauditor/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/wjduenow/clauditor/releases/tag/v0.1.1
[0.1.0]: https://github.com/wjduenow/clauditor/releases/tag/v0.1.0
