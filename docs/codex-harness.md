# Codex harness

This doc covers running your skill under the OpenAI **Codex** CLI instead of Claude Code. If you just want to grade Anthropic-authored skills with Claude as the runtime, you can skip this — the default `--harness auto` resolves to `claude-code` whenever the `claude` binary is on PATH.

> Returning from the [root README](../README.md). This doc is the full reference for the harness axis; the README has a one-paragraph summary.

The harness axis is **independent** of the grader provider axis. A common shape: skill runs under Codex (`--harness codex`), and the L3 grader still calls Claude Sonnet (`--grading-provider anthropic`). The two CLIs are evaluating each other.

## When to use it

- You don't have a Claude Code subscription and don't want one — Codex bills against `OPENAI_API_KEY` directly.
- You want to compare how the same skill behaves under different runtimes (e.g. baseline A/B between Claude and Codex).
- You're auditing a skill author's claim that their SKILL.md is harness-agnostic.

## Quick start

```bash
# 1. Install the Codex CLI separately (npm install -g @openai/codex or similar).
$ which codex
/usr/local/bin/codex

# 2. Export an auth env var. Either works; CODEX_API_KEY wins if both are set.
$ export CODEX_API_KEY=sk-...

# 3. Run a skill under Codex.
$ clauditor validate path/to/SKILL.md --harness codex
$ clauditor grade    path/to/SKILL.md --harness codex
$ clauditor capture  path/to/SKILL.md --harness codex --output codex-run.txt
```

## Four-layer harness resolution

Same precedence shape as `--transport` and `--grading-provider`:

| Layer | Value | Notes |
| --- | --- | --- |
| CLI flag | `--harness {claude-code,codex,auto}` | On `validate`, `grade`, `capture`, `run` only. LLM-mediated commands (`extract`, `triggers`, `compare --blind`, `propose-eval`, `suggest`) have no harness axis — they call `call_model` directly without running a skill subprocess. |
| Env var | `CLAUDITOR_HARNESS={claude-code,codex,auto}` | Whitespace-only values are normalized to "unset". |
| Spec field | `EvalSpec.harness: str = "auto"` | Per-skill author preference. Validated at load time. |
| Default | `"auto"` | `shutil.which("claude")` first; then `shutil.which("codex")`; hard-fail with a three-escape-hatch error if neither is on PATH. |

When the `auto` branch lands on `codex` because `claude` is not on PATH, clauditor emits a one-time stderr notice per process pointing at the env vars and explicit-pin escape hatches — see "Implicit-coupling announcements" in `.claude/rules/centralized-sdk-call.md`. An explicit `--harness codex` (or any non-auto value) stays silent.

## Auth precedence inside the Codex subprocess

The harness exports auth into the skill subprocess in this order:

1. `CODEX_API_KEY` — Codex-specific key. Wins if set.
2. `OPENAI_API_KEY` — fallback. Codex CLI accepts this directly.
3. Cached `~/.codex/auth.json` — only honored when `auth_mode == "apikey"`. Clauditor **refuses** ChatGPT-mode credentials at pre-flight per [#177](https://github.com/wjduenow/clauditor/issues/177) because the codex subprocess would route via ChatGPT and reject every model.

If none of the three is available, the pre-flight `check_codex_auth` guard raises `CodexAuthMissingError` and the CLI exits 2 with an actionable message naming the required env vars.

## Sandbox modes

Codex supports three sandbox levels: `read-only`, `workspace-write`, `danger-full-access`. Clauditor pins `workspace-write` today — enough for skills that need to write under the workspace but not enough for skills that need network or arbitrary filesystem access. The pinned value is stamped on `IterationContext.sandbox_mode` for audit visibility. Making it user-configurable is a future ticket; if your skill needs a different mode, file an issue.

## Stream parser

Codex emits NDJSON on stdout (different from Claude's stream-json). The parser is in `src/clauditor/_harnesses/_codex.py`; the on-disk contract — event types, failure surface, `harness_metadata` keys, advisory warnings — is documented at [docs/codex-stream-schema.md](codex-stream-schema.md). You shouldn't need to read it unless you're contributing to the harness itself or debugging a parse-level failure.

## What lands on disk

Every Codex run produces the same sidecar shape as Claude Code runs, with one observability difference:

- `assertions.json` — `harness: "codex"`.
- `grading.json` / `extraction.json` — `harness: "codex"`, plus the grader's provider/model untouched.
- `context.json` — `harness: "codex"`, `sandbox_mode: "workspace-write"`, plus `harness_metadata` carrying Codex-specific keys (auth source, dropped-events count when applicable).
- `history.jsonl` — each appended record carries `harness: "codex"` so `clauditor trend` can refuse to average across harnesses unless `--cross-harness` is passed.

## Limitations

- **No subscription path.** Anthropic users with a Claude Pro/Max subscription can grade for free via `--transport cli`; Codex has no equivalent — every call bills against an API key.
- **Hardcoded sandbox.** `workspace-write` only. Future ticket.
- **No allow-hang heuristic.** The Claude Code harness has a `allow_hang_heuristic` knob that classifies "model returned a question" as a failure; Codex doesn't expose the parse hooks needed for it. The flag is silently a no-op when the resolved harness is Codex.
- **Pytest fixtures.** `clauditor_runner` and `clauditor_spec` both honor the harness axis; the eager `check_codex_auth` guard fires at fixture-setup time so a missing-key surfaces as a test-setup error rather than a deep subprocess failure mid-run.

## Pairing harness with grader

The four useful combinations:

| Harness | Grader | When to use |
| --- | --- | --- |
| `claude-code` | `anthropic` | Default. Subscription-friendly if you have Claude Pro/Max. |
| `claude-code` | `openai` | Audit Claude-authored skills with a non-Claude grader to reduce same-vendor bias. |
| `codex` | `anthropic` | Anthropic-graded evaluation of Codex behavior. Common when comparing runtimes. |
| `codex` | `openai` | All-OpenAI stack. |

Resolve provider per [docs/transport-architecture.md](transport-architecture.md) and pair via independent flags — clauditor will refuse to silently average across mismatched harness OR provider axes during `clauditor trend` / `clauditor compare`.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Exit 2 — `CodexAuthMissingError` | Neither `CODEX_API_KEY` nor `OPENAI_API_KEY` set; no usable cached auth | Export one of the two env vars. |
| Exit 2 — "ChatGPT-mode credentials refused" | `~/.codex/auth.json` has `auth_mode == "chatgpt"` | Re-authenticate Codex with an API key flow, OR export `CODEX_API_KEY` to override the cached creds. |
| Auto resolved to codex unexpectedly | `claude` not on PATH | Install Claude Code, or pin `--harness=claude-code` / `CLAUDITOR_HARNESS=claude-code`. |
| "stderr line about auto→codex" | First time the auto branch picked Codex this process | One-shot notice. Pin explicitly to silence. |
| Skills that worked under Claude fail under Codex | Different sandbox / runtime semantics | Expected — that's what cross-harness evaluation reveals. The skill is harness-coupled. |

See also `clauditor doctor` for environment-level diagnostics (which binaries are on PATH, which env vars are set, which auth paths resolve).
