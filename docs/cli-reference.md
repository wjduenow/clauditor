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

The agentskills.io spec defines the frontmatter keys `name`, `description`, `license`, `compatibility`, `metadata`, and `allowed-tools`. Unknown keys normally trigger `AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY` (warning). Two Claude Code extension keys are allowlisted and do NOT trigger the warning: `argument-hint` and `disable-model-invocation`. `argument-hint` is a Claude Code UI hint (shows in slash-command autocomplete, e.g. `argument-hint: "[skill-path]"`) but is **not part of the agentskills.io spec** — there is currently no spec-level standard for declaring skill arguments. The allowlist is maintained by the maintainer-only `/review-agentskills-spec` skill, which periodically diffs Claude Code's published frontmatter documentation against the `KNOWN_CLAUDE_CODE_EXTENSION_KEYS` constant in `src/clauditor/conformance.py` (per DEC-009 and DEC-013 of `plans/super/71-agentskills-lint.md`).

Unquoted `: ` (space-colon-space) inside a frontmatter scalar value triggers `AGENTSKILLS_FRONTMATTER_UNQUOTED_COLON_IN_SCALAR` (error, exit 2) — strict YAML parsers (PyYAML, GitHub's renderer) treat such values as nested mappings while clauditor's permissive reader accepts them silently. Wrap the value in double quotes to silence it (per DEC-003 and DEC-007 of `plans/super/80-strict-frontmatter-yaml.md`).

### Soft-warn hook on every skill load

Beyond the standalone `lint` command, `SkillSpec.from_file` calls `check_conformance` on every skill load and emits **warnings only** to stderr with the `clauditor.conformance:` prefix. Errors are silent at that seam — they surface when the user runs `clauditor lint`. The hook never blocks `from_file`; a skill that would fail `lint` today still loads for `validate`, `grade`, and downstream commands (per DEC-003).

### See also

- **`/review-agentskills-spec`** — the maintainer-only sibling skill (lives at repo-root `.claude/skills/`, not shipped in the wheel, not installed by `clauditor setup`). **`clauditor lint`** checks a user's skill against the spec; **`/review-agentskills-spec`** audits the upstream spec itself (and Claude Code's frontmatter documentation) for drift against clauditor's enforcement. Two sides of the same check: one catches user skills that drift from the spec, the other catches clauditor's enforcement drifting from the spec.

## capture

Run a skill via `claude -p` and save its output to a file. The primary use case is grounding `clauditor propose-eval` in real skill output before bootstrapping the eval spec — a proposer that sees what the skill actually emits writes much tighter assertions than one working from the SKILL.md alone.

```bash
clauditor capture <skill>           # save to tests/eval/captured/<skill>.txt
clauditor capture <skill> -- args   # pass initial context to the skill
clauditor capture <skill> --no-api-key --timeout 600  # subscription auth, 10-min watchdog
```

### Required inputs

- `<skill>` (positional) — skill name (leading slash optional, e.g. `find-restaurants` or `/find-restaurants`). Resolved against the project's `.claude/skills/` and `.claude/commands/` directories the same way `clauditor validate` does.

### Flags

| Flag | Purpose |
| ---- | ------- |
| `-- <args>` | Arguments passed to the skill command as initial context, appended to the `-p` prompt: `claude -p "/<skill> <args>"`. The only injection point before the skill runs — see [Interactive skills](#interactive-skills) below. |
| `--out PATH` | Write captured output to a custom path instead of the default `tests/eval/captured/<skill>.txt`. |
| `--versioned` | Append `-YYYY-MM-DD` to the output file stem (e.g. `my-skill-2026-04-22.txt`). Useful when keeping a dated history of captures. |
| `--no-api-key` | Strip `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the subprocess environment. Falls back to subscription auth cached in `~/.claude/`. Useful for skills that exhaust the API-tier rate limit. |
| `--timeout SECONDS` | Override the default 300-second watchdog. Long-running skills (deep research, multi-step workflows) may still need 600 s or more. |
| `--claude-bin PATH` | Path to the `claude` CLI (default: `claude` on PATH). |

### Default output path

`tests/eval/captured/<skill>.txt` — the same directory `clauditor propose-eval` checks first during [capture discovery](cli-reference.md#capture-discovery-dec-001). Running `capture` before `propose-eval` is all it takes; the proposer picks up the file automatically.

### Interactive skills

`clauditor capture` runs `claude -p "/<skill> <args>"` — a **strictly single-turn, non-interactive** invocation. There is no stdin, no multi-turn conversation, and no way to inject answers to mid-run questions. If the skill asks the user a question mid-run (e.g. "Confirm? yes/no"), Claude emits the question and exits. The runner detects this via the **interactive-hang heuristic** (trailing `?` on the output, or an `AskUserQuestion` tool-use block in the stream-json) and surfaces:

```
WARNING: interactive-hang: skill may have asked for input
         — ensure all parameters are in test_args (heuristic)
```

The output file is still written with whatever the skill produced before the hang.

**For eval-friendly skills:** put all decision context in `-- args` (passed upfront) rather than in mid-run prompts. In the eval spec, mirror this via the `test_args` field — `clauditor validate` and `clauditor grade` use `test_args` as the same initial argument string.

**No spec-level args standard exists yet.** Claude Code adds an `argument-hint` frontmatter key (e.g. `argument-hint: "[test|full]"`) that displays a hint in the slash-command autocomplete UI, but this is a Claude Code extension — it is not in the [agentskills.io specification](https://agentskills.io/specification), carries no validation, and does not distinguish required from optional args. clauditor allowlists it to suppress a spurious lint warning (see [`lint`](#lint)). A proper `arguments` declaration in the agentskills.io spec — covering name, required/optional, type, and default — is tracked in [issue #93](https://github.com/wjduenow/clauditor/issues/93).

### Relationship to `propose-eval` and `validate`

```
clauditor capture my-skill -- "initial context"    # 1. capture real output
clauditor propose-eval my-skill.md                 # 2. bootstrap eval from SKILL.md + capture
clauditor validate my-skill.md                     # 3. tighten and verify L1 assertions
```

`propose-eval` reads the capture automatically from `tests/eval/captured/`; no flag needed. See [Capture discovery](#capture-discovery-dec-001).

### Exit codes

| Code | Meaning |
| ---- | ------- |
| `0` | Skill ran and output was written (even if the skill itself reported an error — the capture records whatever the skill emitted). |
| `1` | Skill subprocess failed to start, timed out, or the runner encountered a fatal stream-json error. Output file is not written. |
| `2` | Input-validation failure — skill name not found, `--out` parent directory does not exist. |

## propose-eval

LLM-assisted EvalSpec bootstrap. `clauditor propose-eval <skill.md>` reads the SKILL.md file and (optionally) a captured skill run, asks Sonnet to propose a full three-layer EvalSpec (L1 assertions, L2 tiered extraction, L3 rubric), validates the proposal through `EvalSpec.from_dict`, and writes a sibling `<skill>.eval.json` next to the SKILL.md (the same path `SkillSpec.from_file` and `clauditor init` auto-discover). Use it to skip the blank-spec drudgery when onboarding a new skill.

Requires authentication: `ANTHROPIC_API_KEY` for API transport, or an authenticated `claude` CLI for CLI transport. See [Authentication and API Keys](#authentication-and-api-keys).

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
| `--transport {api,cli,auto}` | Route the Anthropic call through the HTTP SDK (`api`), the `claude -p` subprocess (`cli`), or the default auto-resolution (`auto` — picks CLI when available). Precedence: this flag > `CLAUDITOR_TRANSPORT` env > `EvalSpec.transport` > default `auto`. Full reference: [docs/transport-architecture.md](transport-architecture.md). Same flag on `grade`, `extract`, `propose-eval`, `suggest`, `triggers`, `compare --blind`. On `grade`, `--transport cli` (explicit flag or `CLAUDITOR_TRANSPORT=cli` env var) implies `--no-api-key` for the skill subprocess so the skill subprocess and grader both use subscription auth end-to-end. When `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` was actually present, a one-time stderr notice announces the strip; with no key in env, the strip is a silent no-op. Pass `--transport api` to keep the keys. |

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

## suggest

LLM-assisted skill improvement. `clauditor suggest <skill.md>` reads the latest `.clauditor/iteration-N/<skill>/grading.json`, asks Sonnet to propose minimal SKILL.md edits keyed to the failing L3 criteria (and any failing L1 assertions captured in the same iteration), hard-validates every proposal's `anchor` as a once-only substring of the current SKILL.md, and writes a unified diff plus a JSON sidecar under `.clauditor/suggestions/`. Use it to close the loop from "grader produced a verdict" to "tool produced an applicable, motivated, anchor-validated edit proposal."

Requires authentication: `ANTHROPIC_API_KEY` for API transport, or an authenticated `claude` CLI for CLI transport. See [Authentication and API Keys](#authentication-and-api-keys).

### Why `suggest` exists

Three guarantees you can't get from a hand-written or free-form-LLM patch:

- **Traceability** — every proposed edit carries `motivated_by: [criterion_id, ...]`, tying the SKILL.md change back to the specific grader signals that motivated it. The proposer rejects at parse time any `motivated_by` id that does not appear in the input's failing-signal lists.
- **Anchor safety** — each `edit_proposal.anchor` is validated to appear **exactly once** in the on-disk SKILL.md (sequentially, so later edits see the mutated buffer earlier edits produced). Any failure aborts the run before writing either file; no partial diffs, no blind replacement.
- **Structured output** — diff + JSON sidecar with `motivated_by`, `anchor`, `replacement`, `rationale`, `confidence`, model/timestamp, `source_iteration`, and `schema_version: 1` for downstream tooling consumers.

### Required inputs

- `<skill_md>` (positional) — path to the SKILL.md file. The command reads the latest iteration under `.clauditor/` that contains `<skill>/grading.json` (skill identity is derived from the SKILL.md via the canonical `derive_skill_name` helper — frontmatter `name:` first, parent-directory name as fallback).

### Flags

| Flag | Purpose |
| ---- | ------- |
| `--from-iteration N` | Override latest-iteration discovery. Loads the grade run from `.clauditor/iteration-N/<skill>/grading.json` (N must be a positive integer). |
| `--with-transcripts` | Include per-run stream-json transcripts in the proposer prompt. Increases token spend; useful when grading-only signals are ambiguous about the failure mode. |
| `--model MODEL` | Override the proposer model (default: `claude-sonnet-4-6`). |
| `--json` | Print the full `SuggestReport` JSON envelope to stdout instead of the unified diff. The sidecar file is still written regardless. |
| `-v, --verbose` | Log bundle size, token counts, duration, and sidecar paths to stderr. |
| `--transport {api,cli,auto}` | Route the Anthropic call through the HTTP SDK (`api`), the `claude -p` subprocess (`cli`), or the default auto-resolution (`auto` — picks CLI when available). Precedence: this flag > `CLAUDITOR_TRANSPORT` env > `EvalSpec.transport` > default `auto`. Full reference: [docs/transport-architecture.md](transport-architecture.md). |

### Examples

```bash
# Basic use — reads the latest iteration's grading.json, writes diff + sidecar.
clauditor suggest .claude/skills/my-skill/SKILL.md

# Preview in CI-friendly JSON form (sidecar still written on disk).
clauditor suggest .claude/skills/my-skill/SKILL.md --json

# Target an older iteration explicitly.
clauditor suggest .claude/skills/my-skill/SKILL.md --from-iteration 3

# Include transcripts for more context when grading-only signals are thin.
clauditor suggest .claude/skills/my-skill/SKILL.md --with-transcripts

# Apply a proposed diff.
git apply .clauditor/suggestions/my-skill-<timestamp>.diff
```

### Sidecar layout

Each invocation that reaches the write step produces two sibling files under `.clauditor/suggestions/`:

- `<skill>-<timestamp>.diff` — a unified diff you can feed to `git apply` (or hand-edit).
- `<skill>-<timestamp>.json` — the structured `SuggestReport` envelope with `schema_version: 1` as the first key. Fields: `skill_name`, `model`, `generated_at` (UTC ISO), `source_iteration`, `source_grading_path`, `input_tokens`, `output_tokens`, `duration_seconds`, `summary_rationale`, `edit_proposals` (list of `{id, anchor, replacement, rationale, confidence, motivated_by, applies_to_file}`), `validation_errors`, `parse_error`, `api_error`.

Timestamps are microsecond-precision UTC (`%Y%m%dT%H%M%S%fZ`); two invocations in the same microsecond would collide (acceptable for v1).

### Exit codes

Mirrors the DEC-008 contract in `src/clauditor/cli/suggest.py`:

| Code | Meaning |
| ---- | ------- |
| `0` | Success — diff (or `--json` envelope) printed to stdout, sidecar written to `.clauditor/suggestions/`. Also `0` when the latest iteration has zero failing signals: Sonnet is NOT called, a stderr note is printed, no sidecar is written (DEC-008 row 2). |
| `1` | Load-time or parse-layer failure: no prior `grading.json` under `.clauditor/`, unreadable SKILL.md, unparseable proposer response, or OS error while writing the sidecar. For the load/parse branches no sidecar is written; for OS write failures, partial sidecar artifacts may remain if one sidecar file (JSON or diff) was written before the error occurred (`write_sidecar` writes the two sibling files sequentially, not atomically). |
| `2` | Anchor-validation failure — one or more proposals named an `anchor` that does not appear exactly once in the current SKILL.md. Errors printed on stderr, no sidecar written. Also `2` when no usable authentication is available (neither `ANTHROPIC_API_KEY` nor an authenticated `claude` CLI). |
| `3` | Anthropic API error — auth failure, rate-limit exhaustion, connection error, or any non-retriable SDK error surfaced by `clauditor._anthropic.call_anthropic`. No sidecar written. |

### Relationship to `grade`

`clauditor grade` is the prerequisite: `suggest` has no standalone mode and no LLM call if no failing signals exist in the discovered iteration. If no `grading.json` exists under `.clauditor/`, the command exits 1 with a hint to run `clauditor grade` first. The bundled `/clauditor` slash command wires this handoff automatically — see [docs/skill-usage.md#proposing-skill-improvements](skill-usage.md#proposing-skill-improvements) for the full walkthrough.

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

## Other commands

The remaining subcommands are documented compactly here. Each accepts `--help` for the live argparse output, which is the canonical source.

### `init`

Generate a starter `eval.json` next to a `SKILL.md`. Reads the skill's frontmatter to infer `skill_name`, then writes a minimal eval spec with no assertions or criteria. Run this once when adopting clauditor for an existing skill, then iterate via `propose-eval`, `validate`, and `grade`.

| Flag | Purpose |
| ---- | ------- |
| `--force` | Overwrite an existing `eval.json` next to the skill. Default behavior is to refuse and exit 1 to protect hand-tuned specs. |

### `validate`

Run Layer 1 deterministic assertions against a skill's output. Spawns the skill via `claude -p`, captures stdout, then evaluates each assertion in `eval.json`. No LLM call on the clauditor side.

| Flag | Purpose |
| ---- | ------- |
| `--eval PATH` | Path to `eval.json`; auto-discovered alongside the skill file if omitted. |
| `--output PATH` | Path to a pre-captured output file. Skips running the skill — useful when iterating on assertions against a single capture. |
| `--json` | Emit results as JSON instead of the human-readable summary. |
| `--no-transcript` | Skip writing per-run stream-json transcripts to disk. |
| `-v / --verbose` | On assertion failure, print the last 5 assistant text blocks to stderr. |

Also accepts the [shared runner flags](#shared-runner-flags-validate-grade-capture-run): `--no-api-key`, `--sync-tasks`, `--timeout`.

### `grade`

Run Layer 3 LLM-graded quality scoring. Auto-increments the iteration slot (`.clauditor/iteration-N/<skill>/`) so historical comparisons stay coherent.

| Flag | Purpose |
| ---- | ------- |
| `--eval PATH` | Path to `eval.json`; auto-discovered if omitted. |
| `--output PATH` | Path to pre-captured output; skips running the skill. |
| `--model MODEL` | Override the grading model (default `claude-sonnet-4-6` or `EvalSpec.grading_model`). |
| `--json` | Emit grading report as JSON. |
| `--dry-run` | Print the grading prompt without making any API call. Cost-free preview. |
| `--variance N` | Run the skill `N` times and grade each; reports cross-run stability. |
| `--iteration N` | Write to a specific iteration slot (default: auto-increment). |
| `--force` | With `--iteration N`, overwrite an existing iteration directory. |
| `--diff` | Compare against the previous iteration's `grading.json`. |
| `--baseline` | After grading, also run `test_args` through Claude **without** the skill prefix and capture baseline L1/L2/L3 sidecars. Roughly doubles LLM cost. |
| `--min-baseline-delta FLOAT` | Gate on the with-skill-vs-baseline pass-rate delta. Exit 1 when the skill underperforms its baseline by more than this margin. |
| `--only-criterion SUBSTRING` | Run only criteria whose name contains the substring (repeatable). Skips the history append. |
| `--transport {api,cli,auto}` | Override the Anthropic call backend. See [transport architecture](#transport-architecture). |

Also accepts the shared runner flags: `--no-api-key`, `--sync-tasks`, `--timeout`, `-v`, `--no-transcript`.

### `run`

Run a skill via `claude -p` and print its output to stdout. The thinnest of the skill-invoking commands — no eval, no assertions, no grading.

| Flag | Purpose |
| ---- | ------- |
| `--args STRING` | Arguments to pass to the skill command. |
| `--project-dir PATH` | Override project-root detection (default: cwd). |
| `--timeout SECONDS`, `--no-api-key`, `--sync-tasks` | Shared runner flags. |

### `extract`

Run Layer 2 schema extraction. Sends the skill output to a small LLM (default Haiku) along with the `sections` schema declared in `eval.json`, then validates the extracted JSON against per-tier field requirements. Produces the `extraction.json` sidecar.

| Flag | Purpose |
| ---- | ------- |
| `--eval PATH` | Path to `eval.json`; auto-discovered if omitted. |
| `--output PATH` | Pre-captured output path; skips the skill subprocess. |
| `--model MODEL` | Override the extraction model. |
| `--json` | Emit results as JSON. |
| `--dry-run` | Print the extraction prompt without making an API call. |
| `-v / --verbose` | Print raw model JSON under failing assertions when available. |
| `--transport {api,cli,auto}` | Override the Anthropic call backend. |

### `triggers`

Test trigger precision: send each `should_trigger` and `should_not_trigger` query in `eval.json` to a small LLM judge that decides whether your skill *should* have fired for that query. Catches over-/under-triggering before users do.

| Flag | Purpose |
| ---- | ------- |
| `--eval PATH` | Path to `eval.json`; auto-discovered if omitted. |
| `--model MODEL` | Override the judge model. |
| `--json` | Emit results as JSON. |
| `--dry-run` | Print sample trigger prompts. |
| `--transport {api,cli,auto}` | Override the Anthropic call backend. |

### `compare`

Diff two iterations or two captured outputs. Three modes:

- `clauditor compare --skill <skill> --from N --to M` — diff iteration `N` vs `M` for a skill (resolves directories under `.clauditor/iteration-*`).
- `clauditor compare <iter-dir-1> <iter-dir-2>` — diff two iteration directories directly.
- `clauditor compare before.txt after.txt --spec <skill.md>` — re-grade two captured outputs and show the delta.

| Flag | Purpose |
| ---- | ------- |
| `--spec PATH` | Path to the skill `.md`; required when diffing `.txt` files. |
| `--eval PATH` | Path to `eval.json`; auto-discovered if omitted. |
| `--skill NAME`, `--from N`, `--to N` | Skill name + iteration numbers (alternative to positional iteration dirs). |
| `--blind` | Run a blind A/B LLM judge over the two outputs and print a preference verdict. Requires `--spec` and `EvalSpec.user_prompt`. |
| `--transport {api,cli,auto}` | Backend selector; only used with `--blind`. |

Exits 1 when a regression is detected (assertion that previously passed now fails, or grading score drops below threshold).

### `audit`

Aggregate per-assertion pass rates across the most recent N iteration workspaces. Surfaces assertions that are flaky, never fire, or fail to discriminate between the with-skill and baseline arms.

| Flag | Purpose |
| ---- | ------- |
| `--last N` | Consider the last `N` iteration directories (default 20). |
| `--min-fail-rate FLOAT` | Flag assertions whose fail rate is at least this value (0.0–1.0). |
| `--min-discrimination FLOAT` | Flag assertions whose with-vs-baseline pass-rate delta is below this value. |
| `--json` | Emit a machine-readable JSON report instead of the table. |
| `--output-dir PATH` | Directory to write audit reports. |

### `trend`

Print a tab-separated history of a metric across iterations, with an ASCII sparkline. Reads `.clauditor/history.jsonl`.

| Flag | Purpose |
| ---- | ------- |
| `--metric PATH` | Metric to trend: `pass_rate`, `mean_score`, or a dotted path into `metrics` (e.g. `total.total`, `grader.input_tokens`). Required unless `--list-metrics` is used. |
| `--list-metrics` | List every available metric path in history for the skill. |
| `--command {grade,extract,validate,all}` | Filter records by subcommand (default `grade`). |
| `--last N` | Show the last `N` records (default 20, must be ≥ 1). |

### `setup`

Install the bundled `/clauditor` slash command by symlinking `.claude/skills/clauditor` → the package's bundled SKILL.md. Idempotent; refuses to overwrite unrelated files or symlinks pointing elsewhere.

| Flag | Purpose |
| ---- | ------- |
| `--unlink` | Remove a previously-installed `/clauditor` symlink. Only removes our own symlinks; refuses to touch unrelated entries. |
| `--force` | Overwrite an existing file or symlink at `.claude/skills/clauditor`. No effect under `--unlink`. |
| `--project-dir PATH` | Override project-root detection; use this directory as the cwd for `.claude/` resolution. |

### `doctor`

Print environment diagnostics: clauditor version, `claude` CLI presence and version, Anthropic SDK version, auth state (API key present? CLI cached creds?), and any common misconfiguration. Takes no flags. Run this first when something feels wrong.

## Shared runner flags (`validate`, `grade`, `capture`, `run`)

Four skill-invoking commands share three flags that control the `claude -p` subprocess the runner spawns. All default to "not set" so today's behavior is unchanged when none are passed.

| Flag | Purpose |
| ---- | ------- |
| `--no-api-key` | Strip both `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the subprocess environment before invoking `claude -p`. The child then falls back to whatever auth is cached in `~/.claude/` — typically a Pro/Max subscription, which carries a much higher throughput ceiling than the API-key tier. Useful for research-heavy skills (multi-agent, deep-research) that exhaust the free API tier in a single run. Non-auth Anthropic env vars such as `ANTHROPIC_BASE_URL` are preserved. Also implied by `--transport cli` on `grade` (see `--transport`). |
| `--timeout SECONDS` | Override the runner's 300-second watchdog for a single invocation. Must be a positive integer; `--timeout 0`, `--timeout -5`, and `--timeout foo` exit `2` at argparse time. Precedence: the CLI flag wins when passed explicitly; otherwise the `EvalSpec.timeout` field wins when set; otherwise the built-in 300s default applies. See [`docs/eval-spec-reference.md#optional-top-level-fields`](eval-spec-reference.md#optional-top-level-fields) for the spec-field side of the contract. |
| `--sync-tasks` | Set `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` in the subprocess env, forcing `Task(run_in_background=true)` spawns to run synchronously. Resolves the output-truncation failure mode for skills that use background sub-agents for parallel fanout. Also suppresses the `background-task:` warning from the [#97](https://github.com/wjduenow/clauditor/issues/97) detector under that env. Precedence: CLI flag > `EvalSpec.sync_tasks` > default `False`. On `grade --baseline`, applies to both primary and baseline arms so the delta compares like-for-like. Synchronous Tasks roughly double wall time vs the parallel default — non-trivial skills routinely land in the 300-400s range and exhaust the built-in 300s watchdog; pair `--sync-tasks` with `--timeout 600` (or higher) to avoid first-run timeouts. **Read the fidelity caveats in [`docs/skill-usage.md#--sync-tasks-force-task-mode-synchronous-at-eval-time`](skill-usage.md#--sync-tasks-force-task-mode-synchronous-at-eval-time) before relying on `--sync-tasks` — you are evaluating a different execution model than what ships.** Full decision record: [`docs/adr/transport-research-103.md`](adr/transport-research-103.md). |

When `claude -p` emits an `apiKeySource` value on its stream-json `init` event, the runner captures it on `SkillResult.api_key_source` and prints one stderr info line of the form `clauditor.runner: apiKeySource=<value>`. Values are labels (`"ANTHROPIC_API_KEY"`, `"claude.ai"`, `"none"`), not secrets. Older `claude` builds that omit the field leave `api_key_source` at `None` and suppress the stderr line — absence is the signal. See [`docs/stream-json-schema.md`](stream-json-schema.md#type-system) for the parser contract.

Under `--transport cli`, each grader subprocess call also emits its own `apiKeySource` line. To distinguish them, the line carries a `(<subject>)` suffix naming the internal LLM call — e.g. `clauditor.runner: apiKeySource=none (L2 extraction)` and `clauditor.runner: apiKeySource=none (L3 grading)` for a `grade` run with tiered sections. Known subjects today: `L2 extraction`, `L3 grading`, `L3 blind compare side1` / `L3 blind compare side2`, `triggers judge`, `suggest proposer`, `propose-eval`. Skill-run subprocesses emit the line without a suffix.

```bash
# Force subscription auth, raise the watchdog to ten minutes.
clauditor grade .claude/commands/deep-research.md --no-api-key --timeout 600

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
| `clauditor grade` | ✓ (with caveat) | Routes through the `claude -p` CLI transport when available. Requires `claude` on PATH + subscription auth; else `ANTHROPIC_API_KEY`. |
| `clauditor grade --variance N` | ✓ (with caveat) | Same as `grade` — each variance rep routes through the selected transport. |
| `clauditor propose-eval` | ✓ (with caveat) | Routes through the CLI transport when available (subscription auth); else `ANTHROPIC_API_KEY`. |
| `clauditor suggest` | ✓ (with caveat) | Routes through the CLI transport when available (subscription auth); else `ANTHROPIC_API_KEY`. |
| `clauditor triggers` | ✓ (with caveat) | Routes through the CLI transport when available (subscription auth); else `ANTHROPIC_API_KEY`. |
| `clauditor extract` | ✓ (with caveat) | Routes through the CLI transport when available (subscription auth); else `ANTHROPIC_API_KEY`. |
| `clauditor compare --blind` | ✓ (with caveat) | Routes the blind A/B judge through the CLI transport when available; else `ANTHROPIC_API_KEY`. |

The "with caveat" rows resolve through the four-layer precedence (CLI flag > env > spec > default `auto`). The `auto` default picks CLI when `claude` is on PATH, else API. Pytest fixtures keep the strict API-key guard unless `CLAUDITOR_FIXTURE_ALLOW_CLI=1` is set. Full details: [docs/transport-architecture.md](transport-architecture.md).

### Error behavior

When neither `ANTHROPIC_API_KEY` is set nor the `claude` CLI is on PATH, the relaxed pre-flight guard (`check_any_auth_available`) fires at exit `2` with an actionable stderr message naming the offending subcommand and both auth paths:

```
ERROR: No usable authentication found.
clauditor grade needs either:
  1. ANTHROPIC_API_KEY exported (API key from https://console.anthropic.com/), OR
  2. claude CLI installed and authenticated (Claude Pro/Max subscription)
Commands that don't need authentication: validate, capture, run, lint, init,
badge, audit, trend.
```

The exit code (`2`) matches the pre-call input-validation category in the [four-exit-code taxonomy](#exit-codes) — the guard fires before any API call is made, so no tokens are spent and no sidecar is written. Pytest fixtures use a strict variant of the guard (`check_api_key_only`) that still requires `ANTHROPIC_API_KEY` unless `CLAUDITOR_FIXTURE_ALLOW_CLI=1` is set — see [docs/transport-architecture.md](transport-architecture.md#auth-state-matrix) for the full matrix.

## Transport architecture

The six LLM-mediated commands above route their Anthropic call through one of two transports: an HTTP SDK path (the `anthropic` Python SDK) or a subprocess path that shells out to the `claude` CLI. The default `auto` resolution picks CLI when `claude` is on PATH (so a Pro/Max subscription alone suffices); operators can force a specific path per-invocation, per-shell, or per-skill.

```bash
# Per-invocation: force API path for this one grade (e.g. CI consistency).
clauditor grade .claude/skills/my-skill/SKILL.md --transport api

# Per-shell / per-CI-job: export CLAUDITOR_TRANSPORT=cli for this session.
export CLAUDITOR_TRANSPORT=cli
clauditor propose-eval .claude/skills/my-skill/SKILL.md

# Per-skill: add "transport": "api" to eval.json for every invocation.
```

**Covered in the full reference:** the two-axis auth-state matrix (API-key × CLI-on-PATH), the four-layer precedence resolution (CLI > env > spec > default), per-category retry ladders and error templates (`rate_limit`, `auth`, `api`, `transport`), the spawn-overhead benchmark (cold vs warm), migration notes for operators who had both transports, and the known limitations (`raw_message=None` under CLI, no cache-token accounting under CLI, `api_key_source` only populated under CLI). Full reference: [docs/transport-architecture.md](transport-architecture.md).

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
| `3`  | Anthropic API error. The Anthropic SDK (or `claude` CLI transport) returned a non-retriable failure — auth error, malformed request, exhausted retries, 5xx, or connection failure. No sidecar is written; re-run once the upstream issue is resolved. |

All six LLM-mediated commands — `grade`, `extract`, `triggers`, `suggest`, `propose-eval`, and `compare --blind` — emit exit 3 on non-retriable Anthropic failures. Pre-call input errors (missing `ANTHROPIC_API_KEY`, oversize token budget) route to exit 2 instead, so CI pipelines can distinguish "fix the input" from "retry later."
