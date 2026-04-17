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
    {"type": "contains", "value": "Venues"},
    {"type": "has_entries", "value": "3"},
    {"type": "has_urls", "value": "3"},
    {"type": "min_length", "value": "500"},
    {"type": "not_contains", "value": "Error"}
  ],

  "sections": [
    {
      "name": "Venues",
      "min_entries": 3,
      "fields": [
        {"name": "name", "required": true},
        {"name": "address", "required": true},
        {"name": "website", "required": true}
      ]
    }
  ],

  "output_file": "research/results.md",
  "output_files": ["research/*.md", "research/*.json"],

  "grading_criteria": [
    "Are all venues within the specified distance?",
    "Are venues appropriate for the specified age range?",
    "Do cost tiers match the budget filter?"
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
      "min_entries": 1,
      "max_entries": 3,
      "fields": [
        {"name": "name",    "required": true},
        {"name": "phone",   "required": true,  "format": "phone_us"},
        {"name": "website", "required": true,  "format": "domain"},
        {"name": "zip",     "required": false, "format": "^\\d{5}$"}
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
