# CLI Reference

Full reference for every `clauditor` subcommand: arguments, flags, the persistent metric history, and the exit codes scripts can key off of. Read this when you need to look up a specific option or wire clauditor into CI, not for the conceptual overview of the three-layer framework.

> Returning from the [root README](../README.md). This doc is the full reference; the README has a summary with code examples.

```bash
clauditor init <skill.md>              # Generate starter eval.json
clauditor lint <skill.md>              # Static agentskills.io spec conformance
clauditor lint <skill.md> --strict     # Treat warnings as exit-2 failures
clauditor lint <skill.md> --json       # JSON envelope for CI
clauditor validate <skill.md>          # Run Layer 1 assertions
clauditor validate <skill.md> --json   # JSON output for CI
clauditor run <skill-name> --args "…"  # Run skill, print output
clauditor extract <skill.md>           # Layer 2 schema extraction
clauditor extract <skill.md> --dry-run # Print extraction prompt only
clauditor grade <skill.md>                             # Layer 3 quality grading (auto-increments iteration)
clauditor grade <skill.md> --variance 3                # Variance measurement
clauditor grade <skill.md> --only-criterion clarity    # Run a subset (repeatable, substring match)
clauditor grade <skill.md> --iteration 5               # Write to .clauditor/iteration-5/<skill>/
clauditor grade <skill.md> --iteration 5 --force       # Overwrite an existing iteration-5/
clauditor grade <skill.md> --diff                      # Compare against prior iteration
clauditor compare --skill <skill> --from 1 --to 2      # Diff two iterations by number
clauditor compare .clauditor/iteration-1/<skill> .clauditor/iteration-2/<skill>  # Diff by directory
clauditor compare before.txt after.txt --spec <skill.md>  # Re-grade two captures
clauditor trend <skill> --metric total.total     # Tab-separated history + ASCII sparkline
clauditor trend <skill> --list-metrics           # List available metric paths
clauditor trend <skill> --metric grader.input_tokens --command extract  # Filter by subcommand
clauditor triggers <skill.md>          # Trigger precision testing
clauditor capture <skill> -- "args"    # Run skill, save stdout to tests/eval/captured/
clauditor audit <skill>                # Aggregate per-assertion pass rates across iterations
clauditor suggest <skill.md>           # Propose SKILL.md edits from prior failing iterations
clauditor propose-eval <skill.md>      # LLM-assisted EvalSpec bootstrap (SKILL.md + optional capture)
clauditor badge <skill.md>             # Shields.io endpoint JSON from latest iteration sidecars
clauditor setup                        # Install the bundled /clauditor slash command symlink
clauditor doctor                       # Report environment diagnostics
```

## lint

Static conformance check against the [agentskills.io specification](https://agentskills.io/specification). `clauditor lint <SKILL.md>` reads the file, parses its YAML frontmatter via the project's `_frontmatter.parse_frontmatter` helper, and runs every rule from the spec (required/optional frontmatter keys, name-vs-parent-dir match, body-line budget, layout expectations) through the pure `check_conformance` helper in `src/clauditor/conformance.py`. The command is **non-LLM** — no tokens spent, no network calls — so it is safe to run on every commit, in CI, and as a pre-publish check before uploading a skill to a registry.

### Required inputs

- `<skill_md>` (positional) — path to the SKILL.md file to lint. Absolute paths are accepted; symlinks are followed to their real target; directories, sockets, and missing paths exit 1.

### Flags

| Flag | Purpose |
| ---- | ------- |
| `--strict` | Treat warnings as failures (exit 2). Errors always exit 2 regardless. Parse failures (`AGENTSKILLS_FRONTMATTER_INVALID_YAML`) always exit 1 even under `--strict` — `--strict` never escalates load-layer parse failures. |
| `--json` | Emit a JSON envelope to stdout instead of the human-readable text. Exit codes are identical to the human-output path. `schema_version: 1` is the first key in the payload. |

### Examples

```bash
# Basic conformance check — exits 0 on pass with a success line.
clauditor lint .claude/skills/my-skill/SKILL.md

# Strict mode — warnings promoted to exit 2 (pre-publish gate).
clauditor lint --strict .claude/skills/my-skill/SKILL.md

# JSON envelope for CI — pipe through jq for programmatic checks.
clauditor lint --json .claude/skills/my-skill/SKILL.md | jq .passed
```

### Exit codes

Non-LLM 0/1/2 taxonomy per `.claude/rules/llm-cli-exit-code-taxonomy.md`:

| Code | Meaning |
| ---- | ------- |
| `0` | Pass — no issues, OR warning-only result without `--strict`. Success line printed to stdout. |
| `1` | Load/parse failure — path does not resolve to a regular file, file is unreadable (OSError / UnicodeDecodeError), or frontmatter is malformed YAML (`AGENTSKILLS_FRONTMATTER_INVALID_YAML`). Never escalated by `--strict`. |
| `2` | Conformance failure — one or more error-severity issues, OR warnings with `--strict` set. Issues rendered on stderr as `clauditor.conformance: <CODE>: <message>`. |

### Claude Code extension allowlist

The agentskills.io spec defines the frontmatter keys `name`, `description`, `license`, `compatibility`, `metadata`, and `allowed-tools`. Unknown keys normally trigger `AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY` (warning). Two Claude Code extension keys are allowlisted and do NOT trigger the warning: `argument-hint` and `disable-model-invocation`. The allowlist is maintained by the maintainer-only `/review-agentskills-spec` skill, which periodically diffs Claude Code's published frontmatter documentation against the `KNOWN_CLAUDE_CODE_EXTENSION_KEYS` constant in `src/clauditor/conformance.py` (per DEC-009 and DEC-013 of `plans/super/71-agentskills-lint.md`).

Unquoted `: ` (space-colon-space) inside a frontmatter scalar value triggers `AGENTSKILLS_FRONTMATTER_UNQUOTED_COLON_IN_SCALAR` (error, exit 2) — strict YAML parsers (PyYAML, GitHub's renderer) treat such values as nested mappings while clauditor's permissive reader accepts them silently. Wrap the value in double quotes to silence it (per DEC-003 and DEC-007 of `plans/super/80-strict-frontmatter-yaml.md`).

### Soft-warn hook on every skill load

Beyond the standalone `lint` command, `SkillSpec.from_file` calls `check_conformance` on every skill load and emits **warnings only** to stderr with the `clauditor.conformance:` prefix. Errors are silent at that seam — they surface when the user runs `clauditor lint`. The hook never blocks `from_file`; a skill that would fail `lint` today still loads for `validate`, `grade`, and downstream commands (per DEC-003).

### See also

- **`/review-agentskills-spec`** — the maintainer-only sibling skill (lives at repo-root `.claude/skills/`, not shipped in the wheel, not installed by `clauditor setup`). **`clauditor lint`** checks a user's skill against the spec; **`/review-agentskills-spec`** audits the upstream spec itself (and Claude Code's frontmatter documentation) for drift against clauditor's enforcement. Two sides of the same check: one catches user skills that drift from the spec, the other catches clauditor's enforcement drifting from the spec.

## propose-eval

LLM-assisted EvalSpec bootstrap. `clauditor propose-eval <skill.md>` reads the SKILL.md file and (optionally) a captured skill run, asks Sonnet to propose a full three-layer EvalSpec (L1 assertions, L2 tiered extraction, L3 rubric), validates the proposal through `EvalSpec.from_dict`, and writes a sibling `<skill>.eval.json` next to the SKILL.md (the same path `SkillSpec.from_file` and `clauditor init` auto-discover). Use it to skip the blank-spec drudgery when onboarding a new skill.

Requires `ANTHROPIC_API_KEY`. See [Authentication and API Keys](#authentication-and-api-keys).

### Required inputs

- `<skill_md>` (positional) — path to the SKILL.md file. The generated spec is written to the sibling `<skill_stem>.eval.json` (e.g. `foo.md` → `foo.eval.json`, `SKILL.md` → `SKILL.eval.json`).

### Flags

| Flag | Purpose |
| ---- | ------- |
| `--from-capture PATH` | Override capture discovery with an explicit file. Wins over `--from-iteration`. |
| `--from-iteration N` | Load the capture from `.clauditor/runs/iteration-N/<skill>/run-0/output.txt` (N must be a positive integer). |
| `--force` | Overwrite an existing sibling `<skill>.eval.json`. Without it, the command refuses with exit 1. |
| `--dry-run` | Print the built proposer prompt to stdout and exit; do not call Anthropic and do not write a file. Cost-free preview. |
| `--model MODEL` | Override the proposer model (default: `claude-sonnet-4-6`). |
| `--json` | Emit the full `ProposeEvalReport` JSON envelope on stdout (includes `schema_version`, tokens, duration, validation errors). |
| `-v, --verbose` | Log capture source, redaction count, model, and token estimates to stderr. |
| `--project-dir PATH` | Override project root (default: cwd). Used for capture discovery and relative-path reporting. |

### Examples

```bash
# Basic bootstrap — uses DEC-001 capture discovery, writes eval.json
clauditor propose-eval .claude/commands/my-skill.md

# Preview the built proposer prompt (no Anthropic call)
clauditor propose-eval .claude/commands/my-skill.md --dry-run

# Bootstrap from an explicit capture file (takes precedence over discovery)
clauditor propose-eval .claude/commands/my-skill.md --from-capture tests/eval/captured/my-skill.txt

# Overwrite an existing eval.json
clauditor propose-eval .claude/commands/my-skill.md --force
```

### Exit codes

Mirrors the DEC-006 contract in `src/clauditor/cli/propose_eval.py`:

| Code | Meaning |
| ---- | ------- |
| `0` | Success — prompt printed (`--dry-run`), report envelope printed (`--json`), or spec written to `eval.json`. |
| `1` | Response-parse failure from the proposer (malformed JSON, missing top-level shape) OR collision: `eval.json` already exists and `--force` was not passed. |
| `2` | Spec-validation failure OR pre-call input error — the proposed dict did not survive `EvalSpec.from_dict` (missing required fields, duplicate ids, invalid `format` strings, …), OR the prompt exceeded the token budget, OR `--from-capture`/`--from-iteration` pointed at a missing/invalid target. Errors printed on stderr. |
| `3` | Anthropic API error — auth failure, rate-limit exhaustion, connection error, or any non-retriable SDK error surfaced by `clauditor._anthropic.call_anthropic`. |

### Capture discovery (DEC-001)

When neither `--from-capture` nor `--from-iteration` is provided, the loader looks for a capture file in this order and uses the first match:

1. `<project_dir>/tests/eval/captured/<skill_name>.txt` (primary — the same directory `clauditor capture` writes to).
2. `<project_dir>/.clauditor/captures/<skill_name>.txt` (fallback).

The `<skill_name>` is resolved from the SKILL.md frontmatter's `name` field, falling back to the containing directory's basename, and finally to the literal string `"skill"` if neither source matches the security regex (`^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`). This clamp blocks path-traversal attempts via a malicious `name:` field (e.g. `"../../etc/passwd"`). If no capture is found, the proposer runs against the SKILL.md alone — the quality of the generated spec will be lower, but the command does not error out. Malformed frontmatter emits a stderr warning and falls through to treating the whole file as body.

### Token budget (DEC-005 / DEC-011)

The prompt is pre-checked against a **50,000-token cap** via a `len(prompt) / 4` heuristic before the Anthropic call. This is a coarse safety rail that prevents a mid-stream 413 when a SKILL.md + capture is pathologically large. Oversize inputs fail fast with an `ERROR:` line on stderr and exit code **2** (pre-call input error per DEC-006) before any Anthropic call is made.

### Security / scrubbing (DEC-008)

Captured skill output is scrubbed through `clauditor.transcripts.redact` before it lands in the prompt OR the sidecar. The redaction is non-mutating (per `.claude/rules/non-mutating-scrub.md`): secret-shaped substrings (Anthropic keys, GitHub PATs, Bearer tokens) are replaced in a new copy while the caller's in-memory representation — if any — stays untouched. Both CLI-override paths (`--from-capture`, `--from-iteration`) apply the same scrub; the loader-discovered path is scrubbed by the loader itself.

### Relationship to `init` and `capture`

- `clauditor init` writes a **skill stub** (`SKILL.md` + starter `eval.json`) for a brand-new skill. Use it first when the skill itself does not yet exist.
- `clauditor propose-eval` fills in an `eval.json` for a skill whose **SKILL.md already exists**. It does not write a skill stub and does not regenerate SKILL.md.
- `clauditor capture <skill> -- "args"` produces the captured run that `propose-eval` reads as grounding context. Capturing before `propose-eval` typically lifts the quality of the generated spec (the proposer sees what real output looks like).

## badge

Generate a [shields.io](https://shields.io)-compatible endpoint JSON from a skill's latest iteration sidecars. `clauditor badge <skill.md>` reads the most recent `.clauditor/iteration-N/<skill>/assertions.json` (L1) and, when present, `grading.json` (L3) / `variance.json`, classifies color and message per the project's rules, and writes the result to `.clauditor/badges/<skill>.json` (or a path passed to `--output`). Point any shields.io endpoint URL at the raw JSON and the rendered SVG updates whenever the JSON changes.

The command is a **read-only aggregator** — it does NOT run the skill and does NOT call Anthropic. Run `clauditor validate` (for L1 coverage) and `clauditor grade` (for L3 coverage) first; the badge surfaces whatever those iterations already produced. When no iteration exists, the command writes a `lightgrey` "no data" placeholder and exits 0 (DEC-001), so CI pipelines that always run `clauditor badge` keep a persistent placeholder even before the first grade.

### Required inputs

- `<skill_md>` (positional) — path to the SKILL.md file. The skill name is derived via the same `skill-identity-from-frontmatter` helper `clauditor validate` and `clauditor grade` use, so the iteration lookup matches what those commands wrote.

### Flags

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--from-iteration N` | latest | Read sidecars from `.clauditor/iteration-N/<skill>/` instead of the latest. N must be a positive integer. A missing N exits 1 with the available-iterations list on stderr (DEC-016). |
| `--output PATH` | `.clauditor/badges/<skill>.json` | Write the badge JSON to PATH. Absolute paths are accepted (DEC-005 — common for GitHub Pages dirs outside the repo). Parent directory must already exist (DEC-022). Mutually exclusive with `--url-only`. |
| `--url-only` | off | Print the Markdown image line to stdout instead of writing JSON. Mutually exclusive with `--output`. |
| `--force` | off | Overwrite an existing badge JSON file. Without it, a collision exits 1 (DEC-011). The DEC-001 lightgrey placeholder write honors `--force` too — it does not silently clobber a "real" badge. |
| `--repo USER/REPO` | git auto-detect | Override the origin-slug auto-detect used by `--url-only` (DEC-002). Falls back to the literal placeholder `USER/REPO` with a stderr warning when auto-detect fails and no override is supplied. |
| `--branch NAME` | git auto-detect | Override the default-branch auto-detect used by `--url-only` (DEC-002). Falls back to `main` with a stderr warning when auto-detect fails. |
| `--label TEXT` | `"clauditor"` | Shields.io badge label text (the left side of the rendered SVG). Rejects `[`, `]`, `(`, `)`, and newlines (would break the Markdown `![alt](url)` syntax) and empty/whitespace values with exit 2. |
| `--style KEY=VALUE` | none | Shields.io style passthrough; repeatable. Whitelist: `style`, `logoSvg`, `logoColor`, `labelColor`, `cacheSeconds`, `link` (DEC-015). Unknown keys emit a stderr warning but still land in the JSON (shields.io silently ignores what it does not know). Values are rejected on control characters or length >512 (DEC-023). `cacheSeconds` is coerced to `int`; non-numeric input exits 2. |
| `-v, --verbose` | off | On success, print a stderr info line naming the written path and iteration (`clauditor.badge: wrote <path> (iteration N)`) per DEC-018. |

### Examples

```bash
# Basic: populate an iteration first, then generate the badge JSON.
clauditor grade .claude/skills/my-skill/SKILL.md
clauditor badge .claude/skills/my-skill/SKILL.md
# → writes .clauditor/badges/my-skill.json (brightgreen "8/8 · L3 92%")

# --url-only: get a Markdown image line for pasting into a README.
clauditor badge .claude/skills/my-skill/SKILL.md --url-only
# → ![clauditor](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/USER/REPO/main/.clauditor/badges/my-skill.json)

# Advanced: shields.io style passthrough, custom label, force-overwrite.
clauditor badge .claude/skills/my-skill/SKILL.md \
  --style cacheSeconds=300 --style style=flat-square \
  --label "my skill" --force
```

### Exit codes

Non-LLM 0/1/2 taxonomy per `.claude/rules/llm-cli-exit-code-taxonomy.md` (the "does not apply" clause — no Anthropic call, so no exit 3):

| Code | Meaning |
| ---- | ------- |
| `0` | Success. Badge JSON written or `--url-only` Markdown image line printed. The DEC-001 / DEC-007 lightgrey placeholder writes (no iteration found, or iteration present but spec declares zero L1 assertions) also return 0. |
| `1` | Runtime failure. Corrupt iteration — iteration dir exists but `assertions.json` is missing (DEC-008). Existing badge JSON without `--force` (DEC-011). `--from-iteration N` referring to a missing iteration (DEC-016). OS-level disk I/O error on write. |
| `2` | Input-validation failure. Mutually exclusive `--url-only` + `--output` both passed (DEC-014). `--output` parent dir does not exist (DEC-022). `--style` malformed (missing `=`, empty key) or value rejected (control characters / length >512) (DEC-015, DEC-023). Skill path missing, not a regular file, or `SkillSpec.from_file` fails to load. |

See also [docs/badges.md](./badges.md) for placement guidance (README vs SKILL.md vs catalog page), the full color-logic table, and a CI integration stub.

## Shared runner flags (`validate`, `grade`, `capture`, `run`)

Four skill-invoking commands share two flags that control the `claude -p` subprocess the runner spawns. Both default to "not set" so today's behavior is unchanged when neither flag is passed.

| Flag | Purpose |
| ---- | ------- |
| `--no-api-key` | Strip both `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the subprocess environment before invoking `claude -p`. The child then falls back to whatever auth is cached in `~/.claude/` — typically a Pro/Max subscription, which carries a much higher throughput ceiling than the API-key tier. Useful for research-heavy skills (multi-agent, deep-research) that exhaust the free API tier in a single run. Non-auth Anthropic env vars such as `ANTHROPIC_BASE_URL` are preserved. |
| `--timeout SECONDS` | Override the runner's 180-second watchdog for a single invocation. Must be a positive integer; `--timeout 0`, `--timeout -5`, and `--timeout foo` exit `2` at argparse time. Precedence: the CLI flag wins when passed explicitly; otherwise the `EvalSpec.timeout` field wins when set; otherwise the built-in 180s default applies. See [`docs/eval-spec-reference.md#optional-top-level-fields`](eval-spec-reference.md#optional-top-level-fields) for the spec-field side of the contract. |

When `claude -p` emits an `apiKeySource` value on its stream-json `init` event, the runner captures it on `SkillResult.api_key_source` and prints one stderr info line of the form `clauditor.runner: apiKeySource=<value>`. Values are labels (`"ANTHROPIC_API_KEY"`, `"claude.ai"`, `"none"`), not secrets. Older `claude` builds that omit the field leave `api_key_source` at `None` and suppress the stderr line — absence is the signal. See [`docs/stream-json-schema.md`](stream-json-schema.md#type-system) for the parser contract.

```bash
# Force subscription auth, raise the watchdog to five minutes.
clauditor grade .claude/commands/deep-research.md --no-api-key --timeout 300

# Pro/Max operator running a fast-failing CI check — CLI wins over spec.
clauditor validate .claude/commands/my-skill.md --no-api-key --timeout 30
```

Pytest integration: `--clauditor-no-api-key` is the plugin-option counterpart to `--no-api-key` on the CLI. It threads the same env scrub through the `clauditor_spec` fixture's `env_override`. The existing `--clauditor-timeout` pytest option continues to control the per-runner default (constructor `timeout=...`); per-invocation overrides flow through the fixture factory just like the CLI path. See [`docs/pytest-plugin.md`](pytest-plugin.md).

## Authentication and API Keys

Anthropic exposes two distinct auth modes, and clauditor commands split cleanly along that line:

- **API key** — pay-per-token, set via the `ANTHROPIC_API_KEY` environment variable. Required by any clauditor command that calls the Anthropic API from the Python process.
- **Claude Pro/Max subscription** — flat-rate plan, credentials cached under `~/.claude/` by the `claude` CLI. Works for commands that only spawn the `claude -p` skill subprocess.

The Python `anthropic` SDK is API-only: it does not read subscription credentials from `~/.claude/`. That is why the LLM-mediated commands below (`grade`, `propose-eval`, `suggest`, `triggers`, `extract`, and `compare --blind`) require `ANTHROPIC_API_KEY` even when the user is signed in to Claude Pro/Max. Commands that only run the skill subprocess work under either auth mode.

### Feature-impact matrix

| Command | Works without API key? | Why |
| ------- | :--------------------: | --- |
| `clauditor validate` | ✓ | L1 deterministic assertions; no LLM call on the clauditor side. |
| `clauditor capture` | ✓ | Runs the skill subprocess only. |
| `clauditor run` | ✓ | Runs the skill subprocess only. |
| `clauditor lint` | ✓ | Static conformance check, no LLM. |
| `clauditor init` | ✓ | Eval-spec scaffold, no LLM. |
| `clauditor badge` | ✓ | Reads persisted sidecars, no LLM. |
| `clauditor audit` | ✓ | Reads persisted sidecars. |
| `clauditor trend` | ✓ | Reads persisted sidecars. |
| `clauditor grade` | ✗ | L3 grader is a direct `anthropic.AsyncAnthropic` call. |
| `clauditor grade --variance N` | ✗ | Each variance rep re-runs the L3 grader. |
| `clauditor propose-eval` | ✗ | Direct SDK call via `_anthropic.call_anthropic`. |
| `clauditor suggest` | ✗ | Direct SDK call via `_anthropic.call_anthropic`. |
| `clauditor triggers` | ✗ | Direct SDK call via `_anthropic.call_anthropic`. |
| `clauditor extract` | ✗ | Direct SDK call via `_anthropic.call_anthropic`. |
| `clauditor compare --blind` | ✗ | Runs the blind A/B judge via `_anthropic.call_anthropic`. |

### Error behavior

When `ANTHROPIC_API_KEY` is unset (or empty / whitespace-only) on one of the ✗-row commands, the CLI exits `2` with an actionable stderr message naming the offending subcommand:

```
ERROR: ANTHROPIC_API_KEY is not set.
clauditor grade calls the Anthropic API directly and needs an API
key — a Claude Pro/Max subscription alone does not grant API access.
Get a key at https://console.anthropic.com/, then export
ANTHROPIC_API_KEY=... and re-run. Subscription support via claude -p
is tracked in #86.
Commands that don't need a key: validate, capture, run, lint, init,
badge, audit, trend.
```

The exit code (`2`) matches the pre-call input-validation category in the [four-exit-code taxonomy](#exit-codes) — the guard fires before any API call is made, so no tokens are spent and no sidecar is written.

### Subscription support (follow-up)

Routing L3 grading, `propose-eval`, `suggest`, `triggers`, `extract`, and `compare --blind` through the `claude -p` subprocess (so a Pro/Max subscription alone is enough) is tracked in [GitHub issue #86](https://github.com/wjduenow/clauditor/issues/86). Until that lands, the LLM-mediated commands above need a direct API key.

## Persistent metric history

Every `clauditor grade`, `extract`, and `validate` run appends a JSON line to `.clauditor/history.jsonl`. Each record carries a `command` discriminator, a nested `metrics` dict, and (for `grade`) the `iteration` slot and on-disk `workspace_path`.

```json
{
  "schema_version": 1,
  "command": "grade",
  "ts": "2026-04-13T15:00:00+00:00",
  "skill": "find-restaurants",
  "iteration": 4,
  "workspace_path": ".clauditor/iteration-4/find-restaurants",
  "pass_rate": 0.83,
  "mean_score": 0.75,
  "metrics": {
    "skill":   {"input_tokens": 1200, "output_tokens": 800},
    "quality": {"input_tokens": 900,  "output_tokens": 350},
    "total":   {"input_tokens": 2100, "output_tokens": 1150, "total": 3250},
    "duration_seconds": 12.3
  }
}
```

Token buckets: `skill` (subprocess), `grader` (Layer 2 extract), `quality` (Layer 3 rubric), `triggers` (trigger precision). Buckets are **absent** when the command doesn't invoke them — e.g. `extract` records have `skill` + `grader`, `validate` records have `skill` only. `total` aggregates across all present buckets.

Use `clauditor trend <skill> --metric <dotted.path>` to view a series. Paths walk the nested `metrics` dict (`total.total`, `skill.output_tokens`, `quality.input_tokens`, `duration_seconds` for grade records; `grader.input_tokens` for extract records) with `pass_rate` and `mean_score` as top-level shortcuts. `--command {grade,extract,validate,all}` filters by subcommand (default `grade`); pass `--command extract` to surface `grader.*` paths. `--list-metrics` prints every resolvable metric path for the skill.

Runs with `--only-criterion` skip the history append to keep longitudinal data comparable.

## Exit codes

clauditor uses structured exit codes so scripts and CI pipelines can distinguish "the tool itself failed" from "the tool ran fine but the skill under test failed its gate."

| Code | Meaning                                                                                                                                              |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0`  | Success. The command completed and, where applicable, the skill passed its gate (all assertions satisfied, all criteria above threshold, no regression detected, no trigger miss). |
| `1`  | Signal failed. The tool ran fine, but the skill did not meet its bar: an L1 assertion failed, an L3 criterion scored below threshold, `clauditor compare` detected a regression relative to baseline, or a trigger classification was wrong. The on-disk artifacts are complete and valid; the skill needs fixing, not the tool. |
| `2`  | Input error. A user-supplied argument was missing, malformed, or incompatible with another flag (e.g. `--iteration` without an integer value, a skill `.md` file that does not exist, an eval spec that fails schema validation). The command exited before doing work; re-run with corrected arguments. |
| `3`  | Anthropic API error. `clauditor suggest` and `clauditor propose-eval`. The Anthropic SDK returned a non-retriable failure (auth, malformed request, exhausted retries). No sidecar is written; re-run once the upstream issue is resolved. |

Commands that only invoke the Anthropic API transiently (`extract`, `grade`, `triggers`) funnel API failures through the same retry policy as `suggest` but surface them as exit 1 with an `ERROR:` line on stderr rather than a distinct code.
