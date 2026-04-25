# ADR: `claude -p` + `Task(run_in_background=true)` transport gap

- **Status**: Accepted — No-go on Tier 3, wait for upstream. Tier 1.5 workaround (env-var force-sync) authorized for partial resolution.
- **Date**: 2026-04-24
- **Issue**: [#103](https://github.com/wjduenow/clauditor/issues/103) Tier 2 research spike.
- **Related**: [#97](https://github.com/wjduenow/clauditor/issues/97) (detect-and-warn, merged `4c51243`); upstream [anthropics/claude-code#52917](https://github.com/anthropics/claude-code/issues/52917) (clauditor-filed, names the `-p` + `run_in_background` gap specifically).

## Context

clauditor invokes skills through `claude -p` (print mode, non-interactive, stream-json output). Skills that launch sub-agents with `Task(run_in_background=true)` fail under this transport: the parent agent emits its immediate output and exits with a valid stream-json `result` before the background sub-agents complete. #97 surfaces this loudly as a `background-task:` warning + non-zero exit, but the underlying transport gap remains — a whole category of skills cannot be evaluated end-to-end under clauditor.

Issue #103 proposes three tiers of response:

- **Tier 1** — documentation + refactoring recipes (cheap, v0.1.x).
- **Tier 2** — this ADR: research spike to decide whether a clauditor-side fix is feasible.
- **Tier 3** — speculative: build an in-clauditor turn-loop emulator that polls background tasks (high cost).

This ADR documents the Tier 2 findings and the go/no-go decision on Tier 3.

## Research questions and findings

### Q1: Does `claude -p` have a wait-for-background-task mechanism?

**No.** Enumerated all flags on `claude 2.1.119` via `claude --help` and `claude -p --help`. No `--wait`, `--poll`, `--wait-for-background`, `--max-wait`, turn-loop override, or anything semantically equivalent. Control flags touch model, tools, output format, session, budget, and input format — nothing about background-task lifecycle.

The `claude agents` subcommand claims to "manage background and configured agents" but exposes only `--setting-sources`, no runtime polling. The stream-json schema emits `result` when the parent agent's turn ends; no `background_task_pending`, `task_pending`, or `awaiting_subagents` event type exists (confirmed against clauditor's permissive parser at `src/clauditor/runner.py:485-527`).

### Q2: Can clauditor inject a synthetic "wait for subagents" turn?

**Technically possible, unlikely to work reliably.**

- **Input channel exists.** `--input-format stream-json` enables realtime stdio streaming; `--replay-user-messages` confirms multi-turn input-over-stdin is supported. `--continue` / `--resume` / `--session-id` allow a second `claude -p` invocation on the same session.
- **Runner refactor is localized but nontrivial.** `src/clauditor/runner.py:514-527` constructs `argv = [claude_bin, "-p", prompt, "--output-format", "stream-json", "--verbose"]` and reads stdout to EOF. Injecting a follow-up turn would require switching to `--input-format stream-json` and keeping stdin open.
- **Semantic risk: background-task state likely lost at `result`.** Upstream [#50572](https://github.com/anthropics/claude-code/issues/50572) documents that background shells die when a subagent's turn ends, even with explicit "do not hand off" prompt instructions. [#40692](https://github.com/anthropics/claude-code/issues/40692) confirms completion notifications arrive *after* the main output stream has closed. Even if clauditor reopens the session with `--resume`, the background processes from the original turn may already be reaped. No upstream guarantee exists that "send another user turn" causes the agent to *wait* rather than *report stale status*.

### Q3: Upstream feature requests?

Five open issues, no PRs, no milestones as of 2026-04-24:

| # | Title |
|---|-------|
| [50572](https://github.com/anthropics/claude-code/issues/50572) | Subagents silently terminate long-running background shells on turn end |
| [48657](https://github.com/anthropics/claude-code/issues/48657) | Fire hooks on background task completion |
| [28221](https://github.com/anthropics/claude-code/issues/28221) | PostTask hook — fire when background agent completes |
| [52856](https://github.com/anthropics/claude-code/issues/52856) | Headless `claude status --json` for current session state |
| [44075](https://github.com/anthropics/claude-code/issues/44075) | SubagentStart hooks not fired for background agents |

None explicitly discuss the `claude -p` + `run_in_background` interaction in clauditor's specific shape (non-interactive, parent emits `result` before children complete). #50572 is the closest match but describes the *interactive* TUI manifestation. #52856 would directly unblock clauditor (poll `claude status --json` for `state == "idle"` and drained task queue) but is unimplemented.

## Decision

**No-go on Tier 3. Wait for upstream.**

Tier 3 would require:

1. Refactoring `SkillRunner._invoke` to use `--input-format stream-json` with persistent stdin. **Feasible.**
2. Implementing a heuristic to decide "is the parent still waiting on children?" **No signal in the stream** — clauditor would have to pattern-match `tool_use` blocks for `Task(run_in_background=true)` and track un-completed `task_id`s, duplicating logic the CLI owns.
3. Injecting synthetic "please wait for background tasks" user turns. **Depends on the model choosing to wait when asked**, which #50572 documents as unreliable even with explicit multi-paragraph prompt constraints.

The engineering effort is high, the semantic reliability is low, and any one of #28221 / #48657 / #52856 landing upstream would make a clean solution trivial.

## Tier 1.5 — env-var force-sync workaround

Post-decision follow-up research (2026-04-24) surfaced a documented Anthropic env var that sidesteps the transport gap for the *parallel fan-out* sub-case: **`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`** (see [sub-agents docs](https://docs.claude.com/en/docs/claude-code/sub-agents) — "Run subagents in foreground or background"). Setting this in the subprocess env forces all Task spawns synchronous, so the parent waits for sub-agents before emitting `result` and clauditor sees the full transcript.

Clauditor already has the plumbing: `env_override` threads through `SkillSpec.run` → `SkillRunner.run(env=...)` per `.claude/rules/spec-cli-precedence.md` (the one-level pass-through anchor that `--no-api-key` uses). A minimal implementation adds:

- `env_with_sync_tasks(base_env)` helper next to `env_without_api_key` in `src/clauditor/runner.py`.
- `--sync-tasks` CLI flag on `validate` / `grade` / `capture` / `run` (the four commands already carrying `--no-api-key`).
- Optional `EvalSpec.sync_tasks` for author-level opt-in (three-level precedence).

### What this resolves

- The *capture* problem: clauditor sees the full transcript, grading runs against complete output, #97's warning path goes silent when the flag is set.
- The **parallel-research-fanout sub-case** (the motivating `find-restaurants --depth deep` shape in #103): sync vs async output is functionally equivalent for skills whose only use of `run_in_background=true` is latency-reduction.

### What this does NOT resolve

- **Fidelity gap.** Under `--sync-tasks`, clauditor evaluates a *different execution model* than production. The skill ships with async; clauditor tests sync.
- **Async-specific logic is not exercised.** Race conditions, late-arriving-result handling, "while sub-agents run, emit progress" branches, and completion-order-dependent dedup/merge logic all go untested.
- **Timing/cost metrics skew.** 3 parallel × 30s = 30s in prod becomes 90s under sync; latency and (where pricing is turn-based) token metrics are unrealistic.
- **The underlying transport gap persists.** Skills whose correctness depends on async semantics still cannot be evaluated end-to-end under clauditor. The real fix still requires upstream support (anthropics/claude-code#52917 / #52856 / #28221).

### Layered user-facing story

With Tier 1.5 landed, the story becomes:

1. **Default: warn.** #97's detect-and-warn fires on `run_in_background=true` without `--sync-tasks`. Users aren't misled by truncated captures.
2. **Opt-in: force sync** via `--sync-tasks` when the skill's sync/async output is functionally equivalent (the common fanout case). Fidelity caveats documented alongside the flag.
3. **Long-term: async fidelity** waits on upstream. When #52917 / #52856 / #28221 lands, revisit for a true fix.

## Consequences

- **Keep #97's detect-and-warn in place** as the default user-facing story. Under `--sync-tasks`, the warning is suppressed.
- **Ship Tier 1** (docs + refactoring recipes) AND **Tier 1.5** (`--sync-tasks` flag) as a combined story: refactoring recipes stay for cases where the author wants to change the skill; the flag serves cases where the author wants to keep async in prod and test sync.
- **Document the fidelity tradeoff** clearly in `docs/skill-usage.md`: `--sync-tasks` is not a transparent equivalent of async execution.
- **Upstream issue filed** at [anthropics/claude-code#52917](https://github.com/anthropics/claude-code/issues/52917).
- **Revisit** when #52856 (headless `claude status --json`) or #28221 (PostTask hook) lands. Either would unblock a clean in-clauditor implementation that does not depend on the model's discretion to wait or on forcing a sync execution model.
