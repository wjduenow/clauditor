# Eval Spec Format

Complete schema walkthrough for `<skill-name>.eval.json`: discovery rules, every supported field, input-file staging, output-file capture, and the `format` validation DSL. Read this when you're authoring or debugging an eval spec; it's the full reference the shorter README teaser points at.

> Returning from the [root README](../README.md). This doc is the full reference; the README has a summary with code examples.

Place `<skill-name>.eval.json` alongside your `.claude/commands/<skill-name>.md`:

```
.claude/commands/
├── find-kid-activities.md
├── find-kid-activities.eval.json    ← clauditor auto-discovers this
├── find-restaurants.md
└── find-restaurants.eval.json
```

**File-based output:** Many skills save results to files instead of printing to stdout. Use `output_file` for skills that write to one known path (e.g., `research/results.md`). Use `output_files` with glob patterns for skills that produce multiple files (e.g., `["research/*.md"]`). If both are set, `output_file` takes precedence. When set, clauditor reads the file(s) after running the skill instead of capturing stdout.

## Input files

Some skills need sample inputs — a CSV to clean, a log file to summarize, a PDF to extract. Declare them with `input_files` and clauditor will stage them into each variance run's working directory before invoking the skill:

```json
{
  "skill_name": "csv-cleaner",
  "test_args": "--strict",
  "input_files": ["fixtures/sales.csv"]
}
```

At grade time, `fixtures/sales.csv` is copied (via `shutil.copy2`) into `.clauditor/iteration-N/csv-cleaner/run-K/inputs/sales.csv` for each of the `variance.n_runs` runs, and the skill's subprocess is launched with that `inputs/` directory as its CWD. So `/csv-cleaner --strict` sees `sales.csv` as a plain basename in its own current directory — no path wrangling required. Each `run-K` gets its own fresh copy, so a skill that mutates its input in one run does not affect the next.

Rules enforced at spec-load time (`EvalSpec.from_file`):

- **Paths are relative to the eval spec's parent directory**, not the repo root. An `input_files` entry of `fixtures/sales.csv` next to `my-skill.eval.json` resolves to `<spec-dir>/fixtures/sales.csv`. This intentionally differs from `output_files`, which globs relative to the skill's working directory.
- **Absolute paths are rejected.** Use a relative path under the spec directory.
- **Source containment is enforced.** The resolved path (including symlink targets) must live under the spec's parent directory. Escapes via `..` or symlinks pointing outside the spec tree raise `ValueError`.
- **Missing files fail loudly.** Paths are resolved with `Path.resolve(strict=True)` — a typo fails at load, not at grade time.
- **Destinations are flattened to basenames.** `input_files: ["data/sales.csv"]` stages as `run-K/inputs/sales.csv`, not `run-K/inputs/data/sales.csv`. Two entries that would flatten to the same basename (e.g. `a/data.csv` and `b/data.csv`) raise `ValueError` at load.
- **Collision guard with `output_files`.** Any literal `output_files` pattern whose basename matches an `input_files` basename raises `ValueError` at load. If your skill mutates `sales.csv` in place and you want to capture the result, either declare the output under a different basename / subdirectory in `output_files`, or read the post-run file back from the persisted `iteration-N/<skill>/run-K/inputs/` directory after grading.
- **No file-size cap.** Files are copied verbatim — eval specs are author-controlled, so keep fixtures reasonable.

**Captured-output mode:** `clauditor grade --output <file>` reads a pre-captured output file instead of running the skill. In that mode, staging is skipped. If a spec declares `input_files` and `--output` is passed, clauditor prints `WARNING: --output bypasses the runner; input_files declaration is ignored.` to stderr and continues.

**Persistence:** staged inputs are preserved post-finalize under `.clauditor/iteration-N/<skill>/run-K/inputs/` alongside `output.txt` and `output.jsonl`, so you can inspect exactly what the skill saw (and what it did to the files) after each run.

**Pytest plugin:** the `clauditor_spec` fixture transparently stages `input_files` into `tmp_path` when a loaded spec declares them, so existing tests need zero changes.

**Security / trust model:** Eval specs are developer-authored and run with the repo owner's filesystem access. Clauditor resolves `input_files` paths under the spec's parent directory, enforces source containment, and rejects absolute paths — but the underlying assumption is that eval specs live in a repo you already trust. Do not run clauditor against eval specs from untrusted sources without reviewing them first.

A complete eval spec with all three layers:

```json
{
  "skill_name": "find-kid-activities",
  "description": "Finds kid-friendly activities near a location",
  "test_args": "\"Cupertino, CA\" --ages 4-6 --count 5 --depth quick",
  "input_files": ["fixtures/sample-venues.csv"],

  "assertions": [
    {"id": "contains_venues", "type": "contains", "needle": "Venues"},
    {"id": "has_entries_3", "type": "has_entries", "count": 3},
    {"id": "has_urls_3", "type": "has_urls", "count": 3},
    {"id": "min_length_500", "type": "min_length", "length": 500},
    {"id": "no_error", "type": "not_contains", "needle": "Error"}
  ],

  "sections": [
    {
      "name": "Venues",
      "tiers": [
        {
          "label": "default",
          "min_entries": 3,
          "fields": [
            {"id": "venue_name", "name": "name", "required": true},
            {"id": "venue_address", "name": "address", "required": true},
            {"id": "venue_website", "name": "website", "required": true}
          ]
        }
      ]
    }
  ],

  "output_file": "research/results.md",
  "output_files": ["research/*.md", "research/*.json"],

  "grading_criteria": [
    {"id": "distance_match", "criterion": "Are all venues within the specified distance?"},
    {"id": "age_appropriate", "criterion": "Are venues appropriate for the specified age range?"},
    {"id": "cost_tier_match", "criterion": "Do cost tiers match the budget filter?"}
  ],
  "grading_model": "claude-sonnet-4-6",
  "grade_thresholds": {
    "min_pass_rate": 0.7,
    "min_mean_score": 0.5
  },

  "trigger_tests": {
    "should_trigger": [
      "Find kid activities in Cupertino",
      "What are some things to do with kids near me?"
    ],
    "should_not_trigger": [
      "What's the weather today?",
      "Help me write a Python script"
    ]
  },

  "variance": {
    "n_runs": 5,
    "min_stability": 0.8
  }
}
```

See [`examples/`](../examples/.claude/commands/example-skill.eval.json) for a complete working eval spec.

## Assertion types and per-type keys

Each Layer 1 assertion carries a `type` plus the per-type semantic keys
listed below (in addition to `id`, `type`, and optional `name`). Integer
fields are native JSON ints, not strings — `{"length": 500}`, not
`{"length": "500"}`. Unknown keys raise `ValueError` at load time with a
"did you mean?" migration hint.

| Type | Required keys | Optional keys | Description |
|---|---|---|---|
| `contains` | `needle` (str) | — | Output contains the needle substring |
| `not_contains` | `needle` (str) | — | Output does NOT contain the needle |
| `regex` | `pattern` (str) | — | Output matches the regex pattern (search, not fullmatch) |
| `min_count` | `pattern` (str), `count` (int) | — | Regex pattern appears at least `count` times |
| `min_length` | `length` (int) | — | Output length is at least `length` chars |
| `max_length` | `length` (int) | — | Output length is at most `length` chars |
| `has_urls` | — | `count` (int, default 1) | Output contains at least `count` URLs |
| `has_entries` | — | `count` (int, default 1) | Output contains at least `count` numbered entries |
| `urls_reachable` | — | `count` (int, default 1) | At least `count` URLs in output return 2xx on HEAD |
| `has_format` | `format` (str) | `count` (int, default 1) | Output contains at least `count` strings matching the format (see [format registry](#field-validation-with-format)) |

Example — one of each shape:

```json
{
  "assertions": [
    {"id": "has_title",      "type": "contains",       "needle": "Results"},
    {"id": "no_error",       "type": "not_contains",   "needle": "Error"},
    {"id": "numbered",       "type": "regex",          "pattern": "\\*\\*\\d+\\."},
    {"id": "three_bullets",  "type": "min_count",      "pattern": "^- ", "count": 3},
    {"id": "long_enough",    "type": "min_length",     "length": 500},
    {"id": "not_too_long",   "type": "max_length",     "length": 5000},
    {"id": "has_3_urls",     "type": "has_urls",       "count": 3},
    {"id": "has_3_entries",  "type": "has_entries",    "count": 3},
    {"id": "urls_work",      "type": "urls_reachable", "count": 2},
    {"id": "two_phones",     "type": "has_format",     "format": "phone_us", "count": 2}
  ]
}
```

## Field validation with `format`

Each `FieldRequirement` accepts a single `format` key that validates the
extracted value. `format` does double duty:

1. **Registered format name** — a shorthand for a built-in regex in the
   format registry. Run `python -c "from clauditor.formats import list_formats; print(list_formats())"`
   to see the full list. Common entries: `phone_us`, `phone_intl`,
   `email`, `url`, `domain`, `date_iso`, `date_us`, `currency_usd`,
   `zip_us`, `percentage`, `ipv4`, `uuid`.
2. **Inline regex** — any string that isn't a registered name is
   compiled with `re.compile` and used as an anchored `fullmatch` against
   the value. Invalid regexes raise `ValueError` at spec load time.

```json
{
  "sections": [
    {
      "name": "Restaurants",
      "tiers": [
        {
          "label": "default",
          "min_entries": 1,
          "max_entries": 3,
          "fields": [
            {"id": "r_name",    "name": "name",    "required": true},
            {"id": "r_phone",   "name": "phone",   "required": true,  "format": "phone_us"},
            {"id": "r_website", "name": "website", "required": true,  "format": "domain"},
            {"id": "r_zip",     "name": "zip",     "required": false, "format": "^\\d{5}$"}
          ]
        }
      ]
    }
  ]
}
```

**`url` vs `domain`:** LLMs commonly extract the display text of markdown
links (`[paesanosj.com](https://paesanosj.com/)` → `paesanosj.com`),
which are valid domains but not URLs with a scheme. Use `format: "url"`
only when you really need `https://…`; use `format: "domain"` to accept
bare hostnames too.

**`max_entries`:** A precision signal — when set, clauditor emits a
`count_max` assertion if extraction returns more entries than the cap.
Field-level checks still run over all extracted entries so you see both
the count failure and any per-entry failures.

## Optional top-level fields

A few `EvalSpec` fields tune specific code paths and are safe to omit:

- **`user_prompt`** (string, default `null`) — a natural-language query
  fed to the blind A/B judge (`blind_compare_from_spec` and the
  `clauditor_blind_compare` pytest fixture). Distinct from `test_args`:
  `test_args` is the CLI argument string passed to the skill subprocess,
  while `user_prompt` is the conversational framing the judge sees when
  comparing two skill outputs. Required only on the blind-compare code
  path; other commands (`validate`, `grade`, `extract`, `triggers`)
  ignore it.
- **`allow_hang_heuristic`** (bool, default `true`) — controls the
  interactive-hang detector in `SkillRunner`. The heuristic flags a
  run as a likely-interactive-hang when the skill stops after one turn
  with a trailing `?` or an `AskUserQuestion` tool call. Set to
  `false` to opt a specific skill out when the heuristic consistently
  mis-classifies its output (e.g. a skill whose correct answer ends
  in a rhetorical question). When disabled, a suppressed-heuristic
  run still lands in `SkillResult` but without the `error_category=
  "interactive"` signal.
- **`grading_model`** (string, default `"claude-sonnet-4-6"`) — the
  Anthropic model used for Layer 3 grading. Override per-spec when you
  want to trade cost for fidelity.
- **`grade_thresholds`** (object, default `null`) — an object with
  `min_pass_rate` and/or `min_mean_score` (both floats in `[0.0, 1.0]`)
  that gate `clauditor grade`'s exit code. When set, a run whose
  metrics fall below either threshold exits `1` (signal failed) rather
  than `0`.
- **`variance`** (object, default `null`) — `{"n_runs": int,
  "min_stability": float}` for `clauditor grade --variance`. Runs the
  skill `n_runs` times, grades each, and asserts cross-run agreement.
- **`trigger_tests`** (object, default `null`) — `{"should_trigger":
  [str, ...], "should_not_trigger": [str, ...]}` for `clauditor
  triggers`. Required by that command; other commands ignore it.
- **`timeout`** (int, default `null`) — per-skill runner timeout in
  seconds. Overrides the built-in 300-second watchdog for skills that
  legitimately need longer (e.g. multi-agent research skills).
  Precedence: `--timeout <seconds>` on the CLI wins when passed
  explicitly; otherwise `EvalSpec.timeout` wins when set; otherwise
  the runner falls back to its 300-second default. Load-time
  validation rejects non-int values (including `true`/`false`) and
  values `<= 0`.

## Schema history

**Issue #67 — per-type assertion keys.** Assertion dicts previously
carried a single overloaded `value` key whose meaning depended on
`type` — a string needle for `contains`, a regex pattern for `regex`, a
stringly-typed count for `has_urls`, and so on. Issue #67 replaced
`value` with per-type semantic keys (`needle`, `pattern`, `length`,
`count`, `format`) and switched integer fields from stringly-typed
(`"value": "500"`) to native JSON ints (`"length": 500`). The loader
rejects the old shape at load time with a "did you mean?" hint pointing
at the correct per-type key. No back-compat window: hand-edit old specs
to the new shape, or run the spec through `clauditor propose-eval
--force` to regenerate it.
