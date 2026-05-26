# Rule: `--json` stdout is PURE JSON — every other line goes to stderr

When a CLI command has a `--json` mode whose stdout is consumed by an
external wrapper that does `JSON.parse(entire stdout)` (today: the npm
`clauditor-eval` subprocess bridge; tomorrow plausibly a CI step, a
GitHub Action, or any tool that pipes `clauditor … --json` into a
parser), **stdout under `--json` must contain ONLY the single JSON
document and nothing else**. Every progress breadcrumb, warning,
"Running …", "Staged N input files", "Skill completed in …" line, and
error render MUST go to **stderr** (or be suppressed) when `--json` is
set. A single stray stdout line turns the consumer's `JSON.parse` into
a hard failure with an opaque "non-JSON output" error.

This is a *cross-cutting* contract: the polluting line is frequently
emitted by a **callee** several layers below the CLI command (e.g.
`SkillSpec.run` printing "Staged …" from inside `cli/validate.py`'s
`--json` path), so it cannot be audited by reading the command
function alone. It bit the #4 npm-wrapper work three times (validate's
two progress lines, then `spec.py`'s staging breadcrumb).

## The pattern

### Command-level: route progress by mode

```python
# cli/validate.py
print(
    f"Running /{spec.skill_name} {spec.eval_spec.test_args}...",
    file=sys.stderr if args.json else sys.stdout,
)
# ... later, the ONLY stdout write under --json:
if args.json:
    print(json.dumps(payload, indent=2))   # pure JSON, nothing before/after
else:
    print(results.summary())
```

### Callee-level: progress breadcrumbs ALWAYS go to stderr

A shared helper like `SkillSpec.run` has no `args.json` visibility and
may be called from both human-facing and `--json` paths. Its
progress/diagnostic output is **unconditionally** stderr — stdout is
reserved for the caller's data:

```python
# spec.py — progress breadcrumb, never stdout.
print(
    f"Staged {len(sources)} input file(s) into {effective_cwd}",
    file=sys.stderr,
)
```

The convention is simply: **stdout = data, stderr = everything else.**
Every `print(...)` in a module reachable from a `--json` code path
either targets `file=sys.stderr`, or is the one intended JSON payload,
or is guarded behind `else:` (the non-`--json` branch).

### Exit-code semantics: `--json` payload carries the result, process returns 0

When the JSON payload itself carries the operation's outcome (an
`exit_code` / `error` / `passed` field), the **process** should return
0 in `--json` mode so the wrapper receives the parsed object as DATA
rather than mapping an arbitrary exit code to a thrown error. The
skill's own exit code lives inside the JSON:

```python
# cli/run.py — run --json
if args.json:
    print(json.dumps(_result_to_json(result), indent=2))
    return 0  # skill's real exit code is in payload["exit_code"]
```

This is distinct from commands where the exit code IS the contract
(`validate --json` returns 0/1 for pass/fail because the wrapper treats
exit 1 as "failing eval is data" per the exit-code taxonomy). The rule:
if the wrapper reads the verdict from the JSON body, don't also encode a
*non-taxonomy* exit code (like a subprocess's -1/124) the wrapper would
misinterpret.

### Wrapper-side: parse stdout defensively, mirror the exit taxonomy

The external consumer treats the producer's stdout as a tolerated
contract (parse defensively, surface a crisp error on non-JSON) and
mirrors the producer's exit-code taxonomy structurally — NOT by
substring-matching messages. See
`src/clauditor/lib/exec.js::mapExit` (npm bridge): exit 0/1 → parsed
JSON data, 2 → `ClauditorInputError`, 3 → `ClauditorApiError`, other →
`ClauditorError`; non-JSON stdout → `ClauditorError` with a bounded
snippet.

## Why this shape

- **`JSON.parse(entire stdout)` is all-or-nothing.** The npm bridge
  (and most `--json` consumers) parse the whole stdout buffer in one
  call. There is no "skip the first line" affordance; one breadcrumb
  breaks the parse for every invocation that hits that code path.
- **The leak is usually in a callee, not the command.** Auditing the
  CLI command function is insufficient — `SkillSpec.run`, the workspace
  stager, the history appender, and the transcript writer are all
  reachable from a `--json` path and each has its own `print`s. The
  cheap defense is the blanket convention "progress → stderr always."
- **stdout/stderr separation is the POSIX contract anyway.** Data on
  stdout, diagnostics on stderr is how every composable Unix tool
  behaves. `--json` just makes the cost of violating it visible.
- **Exit-code-as-data vs exit-code-as-verdict is a real fork.** `run`
  produces a result object (exit code is data → return 0); `validate`
  produces a verdict (exit 0/1 is the verdict → return it). Conflating
  them either hides failures or makes the wrapper throw on valid data.

## What NOT to do

- Do NOT emit ANY non-JSON line to stdout under `--json` — not a
  progress line, not a warning, not a deprecation notice, not a blank
  line. Route them to stderr.
- Do NOT assume the command function is the only stdout writer. Grep
  every module on the `--json` call path for `print(` without
  `file=sys.stderr` (and for multi-line prints, check the `file=`
  kwarg on a later line).
- Do NOT return a subprocess's raw exit code (-1, 124, 137) as the
  process exit code in `--json` mode when the payload already carries
  it — the wrapper's exit-taxonomy mapper will turn valid data into a
  thrown error.
- Do NOT have the wrapper substring-match stderr/stdout to classify
  outcomes; mirror the producer's exit-code taxonomy structurally.

## Canonical implementation

- `src/clauditor/cli/run.py::cmd_run` — `--json` prints one JSON
  object and `return 0` (skill exit code in `payload["exit_code"]`);
  error render goes to stderr.
- `src/clauditor/cli/validate.py::cmd_validate` — "Running …" /
  "Skill completed …" route to stderr under `--json`; the
  skill-failed-to-run branch emits a `{passed:false, results:[],
  error, error_category}` JSON object on stdout (so exit 1 is still
  parseable data); the final happy-path JSON is the only other stdout
  write.
- `src/clauditor/spec.py::SkillSpec.run` — the "Staged N input
  file(s)" breadcrumb is unconditionally `file=sys.stderr`.
- `npm/lib/exec.js::execJson` / `mapExit` — the consumer side: parses
  all of stdout as JSON, mirrors the 0/1/2/3 exit taxonomy, throws
  `ClauditorError` (with a bounded snippet) on non-JSON stdout.

Regression tests: `tests/test_cli.py::TestCmdRun::test_run_json_returns_zero_even_on_nonzero_skill_exit`,
`tests/test_cli.py::TestCmdValidate::test_validate_json_emits_json_when_skill_fails_to_run`,
`tests/test_spec.py` (asserts "Staged …" is on stderr, not stdout).

Traces to the #4 npm-wrapper Quality Gate (epic `clauditor-ovml`,
DEC-004/DEC-008/DEC-009 of `plans/super/4-npm-wrapper.md`).

## Companion rules

- `.claude/rules/llm-cli-exit-code-taxonomy.md` — the 0/1/2/3 exit
  taxonomy the wrapper mirrors and the exit-code-as-verdict shape.
- `.claude/rules/stream-json-schema.md` — the defensive-parse posture
  for an external streaming format, applied here to the wrapper's
  stdout-JSON parse.
- `.claude/rules/pure-compute-vs-io-split.md` — `_result_to_json` /
  `mapExit` are the pure projections; the I/O layer owns stream choice.

## When this rule applies

Any CLI command that gains a `--json` (or other machine-readable)
output mode intended for a programmatic consumer. The moment a wrapper,
CI step, or pipe does `parse(stdout)`, every module on that command's
call path is bound by the stdout-purity contract.

## When this rule does NOT apply

- Human-facing output modes (no `--json`), where interleaving progress
  and results on stdout is fine.
- Commands whose `--json` output is a JSONL stream (one object per
  line) AND the consumer reads line-by-line — there the per-line
  contract differs (still no non-JSON lines, but the consumer tolerates
  multiple documents). See `.claude/rules/stream-json-schema.md`.
- Debug/diagnostic dumps with no programmatic consumer.
