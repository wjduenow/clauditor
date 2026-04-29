# Architecture Diagrams

Visual + narrative reference for how `clauditor grade` flows end-to-end and how the three evaluation layers compose. Read this when you want depth beyond the README's summary — the grade-command flow, the three-layer pipeline with subgraphs, and the cost/fidelity tradeoffs per layer.

> Returning from the [root README](../README.md). This doc is the full reference; the README has a summary with code examples.

## Overview

At a glance, a `clauditor grade` run invokes the skill, then fans the
skill's output into three independent evaluation layers whose results
are persisted and reported together:

```mermaid
flowchart LR
    A["clauditor grade\nskill.md"] --> B["Run skill\n(claude -p)"]
    B --> C["Skill output"]
    C --> D["L1 Assertions\n(free)"]
    C --> E["L2 Extraction\n(Haiku)"]
    C --> F["L3 Quality\n(Sonnet)"]
    D --> G["Persist + Report"]
    E --> G
    F --> G

    style D fill:#c8e6c9
    style E fill:#fff9c4
    style F fill:#ffccbc
```

Results land under `.clauditor/iteration-N/<skill>/` and are appended to
`history.jsonl` for trend tracking. The sections below expand each
stage: §1 walks through the full command end-to-end, §2 details the
three-layer pipeline.

## 1. Grade Command — End-to-End Flow

What happens when you run `clauditor grade skill.md`:

```mermaid
flowchart TD
    A["clauditor grade skill.md"] --> B["Load SkillSpec + EvalSpec\n(spec.py, schemas.py)"]
    B --> C["Allocate iteration workspace\n.clauditor/iteration-N-tmp/&lt;skill&gt;/"]
    C --> D["Run skill via subprocess\n(runner.py)"]

    D --> E["claude -p '/{skill} {test_args}'\n--output-format stream-json --verbose"]
    E --> F["Parse NDJSON stream\ncapture text + tokens + events"]
    F --> G["SkillResult\noutput, tokens, duration, stream_events"]

    G --> H["Layer 1: Assertions\n(assertions.py)"]
    G --> I["Layer 2: Extraction\n(grader.py)"]
    G --> J["Layer 3: Quality Grading\n(quality_grader.py)"]

    H --> K["assertions.json"]
    I --> L["extraction.json"]
    J --> M["grading.json"]

    K --> N["Write sidecars to\niteration workspace"]
    L --> N
    M --> N

    N --> O["Atomic rename\niteration-N-tmp → iteration-N"]
    O --> P["Print report to stdout\nAppend to history.jsonl"]

    style D fill:#e1f5fe
    style H fill:#c8e6c9
    style I fill:#fff9c4
    style J fill:#ffccbc
    style O fill:#f3e5f5
```

### Key details

| Step | What | Where |
|------|-------|-------|
| Subprocess | `claude -p` with stream-json output | `_harnesses/_claude_code.py::ClaudeCodeHarness.invoke` (called from `runner.py::SkillRunner._invoke`) |
| L1 Assertions | Deterministic string matching — no API calls | `assertions.py::run_assertions` |
| L2 Extraction | Schema field extraction via Haiku | `grader.py::extract_and_report` |
| L3 Quality | Rubric-based grading via Sonnet | `quality_grader.py::grade_quality` |
| Persistence | Atomic workspace with sidecars | `workspace.py` + `cli/grade.py` |
| History | One JSONL line per run for `clauditor trend` | `history.py::append_record` |

### Optional phases

- **`--variance N`**: Runs the skill N additional times, aggregates scores across all runs
- **`--baseline`**: Runs a second pass without the skill prefix, grades both, diffs via `compute_benchmark`
- **`--no-transcript`**: Skips writing `run-K/output.jsonl` stream captures

---

## 2. Three-Layer Evaluation Pipeline

How clauditor evaluates a skill's output through three independent layers:

```mermaid
flowchart LR
    subgraph Input
        OUT["Skill output text"]
        SPEC["EvalSpec\n(eval.json)"]
    end

    subgraph "Layer 1 — Deterministic"
        direction TB
        L1["Assertions Engine"]
        L1_IN["assertions[]:\ncontains, regex, min_count,\nhas_urls, custom"]
        L1_OUT["AssertionSet\nper-assertion pass/fail\nno API cost"]
        L1_IN --> L1 --> L1_OUT
    end

    subgraph "Layer 2 — Schema Extraction"
        direction TB
        L2["Haiku Extractor"]
        L2_IN["sections[].tiers[].fields[]:\nname, required, format"]
        L2_OUT["ExtractionReport\nper-field presence + format\nlow API cost"]
        L2_IN --> L2 --> L2_OUT
    end

    subgraph "Layer 3 — Quality Grading"
        direction TB
        L3["Sonnet Judge"]
        L3_IN["grading_criteria[]:\ncriterion text, id"]
        L3_OUT["GradingReport\nper-criterion score + evidence\nhigher API cost"]
        L3_IN --> L3 --> L3_OUT
    end

    OUT --> L1
    OUT --> L2
    OUT --> L3
    SPEC --> L1_IN
    SPEC --> L2_IN
    SPEC --> L3_IN

    L1_OUT --> FINAL["Combined Result\npass_rate, mean_score\nassertion + extraction + grading details"]
    L2_OUT --> FINAL
    L3_OUT --> FINAL

    style L1 fill:#c8e6c9
    style L2 fill:#fff9c4
    style L3 fill:#ffccbc
```

### Layer comparison

| | Layer 1 | Layer 2 | Layer 3 |
|---|---------|---------|---------|
| **What** | Pattern matching | Schema extraction | Rubric grading |
| **How** | Regex, string ops | LLM (Haiku) | LLM (Sonnet) |
| **Cost** | Zero (no API) | Low (~$0.001/run) | Medium (~$0.01/run) |
| **Speed** | Instant | ~1-2s | ~3-5s |
| **Checks** | "Output contains X" | "Output has field Y in format Z" | "Output quality meets criterion C" |
| **Spec key** | `assertions[]` | `sections[].tiers[].fields[]` | `grading_criteria[]` |
| **Output** | `AssertionSet` | `ExtractionReport` | `GradingReport` |
| **Sidecar** | `assertions.json` | `extraction.json` | `grading.json` |

### When each layer runs

- **L1** always runs (if `assertions` defined in eval spec)
- **L2** only runs when `sections` are defined in the eval spec
- **L3** always runs (if `grading_criteria` defined — required for `grade`)
- All three layers receive the **same skill output text** and evaluate independently
- Results are combined into the final report; the overall pass/fail is driven by L3's `pass_rate` against the configured threshold (default 70%)

---

## 3. Harness Protocol

`clauditor.runner.SkillRunner` is harness-agnostic — it delegates the
actual LLM-CLI subprocess to a `Harness` implementation that satisfies
the structural protocol defined in
`src/clauditor/_harnesses/__init__.py`. The protocol has three
members: `invoke(prompt, *, cwd, env, timeout, model, subject)` for
the subprocess + parse loop, `strip_auth_keys(env)` for harness-specific
auth-env scrubbing, and **`build_prompt(skill_name, args, *,
system_prompt) -> str`** (introduced in #150) which composes the wire
prompt each harness's `invoke` expects. Each harness owns its
identity-to-prompt strategy; the runner never assembles a slash command
or message body itself.

Shipping implementations:

- **`ClaudeCodeHarness.build_prompt`** — returns
  `f"/{skill_name} {args}"`, or `f"/{skill_name}"` when `args` is the
  empty string. Ignores `system_prompt` because the `claude -p` CLI
  has no separate system-prompt channel (skill identity carries the
  body via the slash command).
- **`MockHarness.build_prompt`** — appends
  `{"skill_name", "args", "system_prompt"}` to `build_prompt_calls` so
  unit tests can assert against the triple, then returns a deterministic
  string of the form `"[mock]<system_prompt or ''>|/<skill_name>
  <args>"` (trailing whitespace stripped). The returned string surfaces
  all three inputs so a test can assert on the rendered prompt without
  inspecting the call list.
- **Future: `CodexHarness.build_prompt` (#149)** — will prepend
  `system_prompt` as the system message and append `args` as the user
  message, matching the OpenAI-style structured-message wire format.
  This is the motivating use case for the `system_prompt` kwarg living
  on the cross-harness protocol surface.

**Resolution flow.** `SkillSpec.run` resolves the effective
`system_prompt` once at the spec layer (explicit `EvalSpec.system_prompt`
> auto-derived `SKILL.md` body — see
[`docs/eval-spec-reference.md#system-prompt`](eval-spec-reference.md#system-prompt))
and threads the resolved string through to the harness via the new
keyword-only `system_prompt` kwarg on `SkillRunner.run(...)`. The kwarg
is keyword-only and placed last so callers cannot accidentally swap it
positionally with the existing `cwd` / `env` / `timeout` kwargs.
Harnesses that have no notion of a separate system prompt (currently
`ClaudeCodeHarness`) MUST still accept and ignore the kwarg —
analogous to how all harnesses accept `model` on `invoke`.
