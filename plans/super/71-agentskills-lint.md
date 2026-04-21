# Super Plan: #71 — agentskills.io spec conformance check for SKILL.md

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/71
- **Branch:** `feature/71-agentskills-lint`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/71-agentskills-lint`
- **Phase:** `devolved`
- **PR:** https://github.com/wjduenow/clauditor/pull/74
- **Epic:** `clauditor-zsf`
- **Sessions:** 1
- **Last session:** 2026-04-21

---

## Discovery

### Ticket summary

**What:** Add a static conformance checker (`clauditor lint`)
that validates a SKILL.md file against the [agentskills.io
specification](https://agentskills.io/specification), plus a
soft-warn hook in `SkillSpec.from_file` so every command that
loads a skill inherits the check. A `--strict` flag escalates
soft warnings to hard-fail.

Three-part shape:

1. **`clauditor lint` command** — explicit, standalone static
   check. Exits 0 pass, 1 load/parse failure, 2 conformance
   failure (non-LLM taxonomy per
   `.claude/rules/llm-cli-exit-code-taxonomy.md`).
2. **Soft-warn hook on `SkillSpec.from_file`** — prints stderr
   warnings for conformance issues on every load; does not
   fail.
3. **`--strict` escape hatch** — escalates warnings to hard
   fail. Default-strict for `lint`, opt-in elsewhere.

**Why:** `clauditor validate` checks *behavioral* output; it
does not check whether the SKILL.md artifact itself conforms to
the published spec. Authors publishing skills to agentskills.io
or similar registries need a pre-flight check. The bundled
`/review-agentskills-spec` skill (from #72) tells maintainers
when the upstream spec drifts; `clauditor lint` is the
user-facing counterpart that tells an author when their skill
drifts from the spec.

**Done when:**
- `clauditor lint <path/to/SKILL.md>` exists and enforces the
  agentskills.io spec rules (per "Rules to enforce" below).
- `SkillSpec.from_file` emits stderr warnings for conformance
  issues encountered during load; no command fails today's
  skills that would produce only warnings.
- `--strict` on `lint` promotes warnings to exit 2.
- Pure helper `check_conformance(...)` lives in
  `src/clauditor/conformance.py` — testable without `tmp_path`.
- Coverage ≥80%, ruff passes, docs updated (cli-reference,
  README, CHANGELOG).

---

### Key findings — codebase scout

#### Frontmatter + skill-name pipeline

- `src/clauditor/_frontmatter.py::parse_frontmatter` — the
  YAML-subset parser. Accepts scalar entries, quoted values, one
  level of nested mappings (`metadata:` block), and raw inline
  strings (`allowed-tools: Bash(*) Read Grep` stored verbatim).
  Raises `ValueError` on missing closing delimiter, bad `key:
  value` shape, empty keys, multi-level nesting. Lint must
  consume this parser's output, not re-implement it.
- `src/clauditor/paths.py::SKILL_NAME_RE` is
  `r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"` — **incompatible with
  the agentskills spec** (allows uppercase, underscores, up to
  128 chars). Conformance must NOT reuse this regex for
  `AGENTSKILLS_NAME_*` checks. The two validators have
  different contracts: `SKILL_NAME_RE` is clauditor's internal
  safety filter (shell-injection / path-traversal); the spec
  regex is the external publish contract.
- `src/clauditor/paths.py::derive_skill_name` — pure helper
  returning `(name: str, warning: str | None)`. Canonical
  precedent for the soft-warn pattern lint must follow. Current
  warnings (invalid frontmatter name, frontmatter-vs-fs
  mismatch) overlap with the new spec-conformance warnings;
  deduplication is a Phase 3 refinement question.
- `src/clauditor/spec.py::SkillSpec.from_file` — reads SKILL.md,
  calls `derive_skill_name`, prints warning to stderr if
  returned, continues. This is the seam the new soft-warn hook
  slots into.

#### CLI subcommand scaffolding

- Subcommand modules live in `src/clauditor/cli/<name>.py`.
  Each exports `add_parser(subparsers)` and `cmd_<name>(args)`.
  Main dispatcher at `src/clauditor/cli/__init__.py` imports
  each module (lines 354-384), registers via
  `<mod>.add_parser(subparsers)` (lines 395-434), and
  dispatches via if-elif on `parsed.command` (lines 452-480).
  14 subcommands currently wired.
- Shared argparse helpers in `cli/__init__.py`: `_unit_float`,
  `_positive_int`. A new `_readable_skill_path(value: str) ->
  Path` helper matching `.claude/rules/path-validation.md`
  would slot in alongside, reusable for future commands.
- Error-rendering helper `_render_skill_error` in
  `cli/__init__.py` is for `SkillResult` failures (behavioral);
  lint produces `ConformanceIssue`s instead. Different renderer
  entirely, but parallel shape.
- Exit-code taxonomy in use today: 0/1/2 for non-LLM commands,
  0/1/2/3 for LLM commands per
  `.claude/rules/llm-cli-exit-code-taxonomy.md`. Lint is
  non-LLM → 0/1/2.

#### Bundled skills' current conformance state

- `src/clauditor/skills/clauditor/SKILL.md` frontmatter:
  `name: clauditor`, `description`, `compatibility`,
  `metadata` (`clauditor-version`), `argument-hint`,
  `disable-model-invocation: true`, `allowed-tools: Bash(...)`.
  **Missing from spec perspective:** `argument-hint` and
  `disable-model-invocation` are not in the agentskills.io
  spec → would flag as `AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY`.
  No `license`.
- `src/clauditor/skills/review-agentskills-spec/SKILL.md` —
  same shape; excluded from `clauditor setup` per
  `.claude/rules/internal-skill-live-test-tmp-symlink.md`.
  Lint should still accept it at the filesystem level
  (internal-only, not user-facing).

#### Test conventions

- Pure helper → separate test file (`tests/test_<module>.py`),
  class-based (`TestCheckConformance`, `TestParseXxx`), no
  `tmp_path` or `capsys` — construct inputs directly.
- I/O-layer (CLI or `SkillSpec.from_file`) → separate class
  using `tmp_path` for file writes and `capsys` for stderr
  assertions.
- `tests/test_paths.py::TestDeriveSkillName` is the closest
  analogue for the pure-helper class, and
  `tests/test_spec.py::TestFromFile` for the I/O-layer class.

---

### Key findings — convention checker

Thirteen of 28 `.claude/rules/*.md` apply. The load-bearing
ones for this ticket:

1. **`pure-compute-vs-io-split.md`** — `check_conformance`
   returns `list[ConformanceIssue]`, no stderr, no I/O. CLI
   layer does file read, stderr emission, exit-code mapping.
2. **`llm-cli-exit-code-taxonomy.md`** — lint is non-LLM → use
   simpler 0/1/2 table. No `AnthropicHelperError`, no exit 3.
3. **`skill-identity-from-frontmatter.md`** — reuse
   `parse_frontmatter` and `derive_skill_name` shapes. Do NOT
   duplicate frontmatter parsing. The existing helper's
   `SKILL_NAME_RE` is different contract (see above).
4. **`path-validation.md`** — path argument to `lint` goes
   through the strict-resolve + containment + is_file recipe.
5. **`constant-with-type-info.md`** — if conformance rules are
   tabulated as a dict, carry `field_types` for type checks.
6. **`json-schema-version.md`** — applies *only* if we emit a
   JSON sidecar (open design decision; see Q5).
7. **`readme-promotion-recipe.md`** — docs update for
   `cli-reference.md` + README teaser; keep H2 anchors
   byte-identical on any moved content.
8. **`bundled-skill-docs-sync.md`** — applies only if the
   bundled `/clauditor` skill workflow grows a lint step;
   otherwise N/A.
9. **`per-type-drift-hints.md`** — per-key "did you mean?"
   hints for unknown frontmatter keys (if we add them).

Not a workflow-project.md rule set; project-specific
customization absent. No existing `--strict` flag precedent in
the codebase — this is the first one.

---

### Key findings — spec extractor

Full spec fetched at https://agentskills.io/specification
(2026-04-21, HTTP 200). Rules enumerated with stable code
names:

#### Required frontmatter

- `AGENTSKILLS_NAME_MISSING` / `_NOT_STRING` / `_EMPTY` /
  `_TOO_LONG` / `_INVALID_CHARS` / `_LEADING_HYPHEN` /
  `_TRAILING_HYPHEN` / `_CONSECUTIVE_HYPHENS` /
  `_PARENT_DIR_MISMATCH` — all severity `error`. The combined
  regex is `^[a-z0-9](?:[a-z0-9]|-(?!-))*[a-z0-9]$` with
  length 1-64 (or 1 char if `^[a-z0-9]$`).
- `AGENTSKILLS_DESCRIPTION_MISSING` / `_NOT_STRING` / `_EMPTY`
  / `_TOO_LONG` — all error. 1-1024 chars.
- `AGENTSKILLS_DESCRIPTION_MISSING_WHEN_CLAUSE` — warning
  (SHOULD).
- `AGENTSKILLS_DESCRIPTION_MISSING_KEYWORDS` — warning (SHOULD).

#### Optional frontmatter

- `AGENTSKILLS_LICENSE_NOT_STRING` — error.
- `AGENTSKILLS_LICENSE_EMPTY` — severity decision (G2).
- `AGENTSKILLS_COMPATIBILITY_NOT_STRING` / `_EMPTY` / `_TOO_LONG`
  — all error. 1-500 chars.
- `AGENTSKILLS_METADATA_NOT_MAP` / `_KEY_NOT_STRING` /
  `_VALUE_NOT_STRING` — all error.
- `AGENTSKILLS_METADATA_KEY_COLLISION_RISK` — warning.
- `AGENTSKILLS_ALLOWED_TOOLS_NOT_STRING` — error.
- `AGENTSKILLS_ALLOWED_TOOLS_EXPERIMENTAL` — warning.

#### Unknown keys + body + layout

- `AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY` — severity decision (G5).
- `AGENTSKILLS_BODY_TOO_LONG` — warning. **500 lines IS in the
  spec** (correction to ticket body). Exact quote under
  `Progressive disclosure`: *"Keep your main SKILL.md under 500
  lines."*
- `AGENTSKILLS_BODY_TOKEN_BUDGET` — warning (< 5000 tokens
  recommended per spec; ticket didn't mention). Requires a
  tokenizer.
- `AGENTSKILLS_SKILL_MD_FILENAME_CASE` — severity decision (G7).
- `AGENTSKILLS_SKILL_MD_NOT_AT_ROOT` — error when layout is
  enforceable (G8).

#### Subdirs (`scripts/`, `references/`, `assets/`)

- `AGENTSKILLS_FILE_REFS_RELATIVE` — warning (SHOULD).
- `AGENTSKILLS_FILE_REFS_DEPTH` — warning (SHOULD).

#### Ticket vs spec mismatches

1. **Ticket says** body 500-line limit is external guidance;
   **spec** has it under `Progressive disclosure`. Elevate
   from "optional check" to "standard warning."
2. **Ticket omits** the `< 5000 tokens` spec guidance. Add as
   separate warning if we take Q4 option B/C.
3. **Ticket qualifies** name/parent-dir match as "for modern
   `<dir>/SKILL.md` layout"; **spec** states it unqualified.
   The qualifier is a clauditor-side accommodation for the
   legacy single-file layout the spec does not address (G8).

#### Ten ambiguities requiring design decisions

| ID | Gap | Decision point |
|---|---|---|
| G1 | `name` char class is spec-internally contradictory | Strict ASCII vs Unicode lowercase |
| G2 | `license` empty-string not explicitly forbidden | Error or silent |
| G3 | `metadata` has no length bounds | Clauditor-side caps? |
| G4 | `allowed-tools` token grammar unspecified | Minimal check or format validation |
| G5 | Unknown top-level frontmatter keys | Error, warning, or silent |
| G6 | Empty body not explicitly forbidden | Accept or warn |
| G7 | `SKILL.md` filename case sensitivity | Strict `SKILL.md` or case-insensitive |
| G8 | Legacy single-file `<name>.md` absent from spec | Skip layout checks, warn, or error |
| G9 | YAML-coerced non-string values (`name: true`) | Strict `isinstance(str)` |
| G10 | YAML auto-coercion in `metadata` (`version: 1.0`) | Strict `isinstance(str)` |

G9 and G10 are the "constant-with-type-info" discipline —
strict `isinstance(str)` is the conventional clauditor choice;
adopt by default.

---

### Proposed scope

1. Pure module `src/clauditor/conformance.py` with
   `ConformanceIssue` dataclass (`code`, `severity`,
   `message`) and `check_conformance(skill_md_text: str,
   skill_path: Path) -> list[ConformanceIssue]`.
2. CLI module `src/clauditor/cli/lint.py` — `add_parser` +
   `cmd_lint(args)`; path validation + file read + call pure
   helper + render + exit-code map; `--strict` flag.
3. Soft-warn hook in `src/clauditor/spec.py::SkillSpec.from_file`
   — call pure helper, emit warnings (severity TBD per Q3) to
   stderr, do not block.
4. Tests: `tests/test_conformance.py` (pure),
   `tests/test_cli.py::TestCmdLint` class (I/O +
   path-validation), `tests/test_spec.py` extension (soft-warn
   hook).
5. Docs: new `## lint` section in `docs/cli-reference.md` +
   quick-reference row; one-line README teaser; CHANGELOG
   entry; cross-link to `/review-agentskills-spec`.
6. Quality Gate (reviewer ×4 + ruff + pytest 80% gate).
7. Patterns & Memory (update rules for the novel `--strict`
   shape and conformance-issue-list shape if they generalize).

---

### Scoping decisions (from Q&A)

- **Q1 — Legacy `<name>.md` single-file layout:** default pass
  with an **explanatory migration warning** (code
  `AGENTSKILLS_LAYOUT_LEGACY`); `--strict` promotes to error.
  The warning message must tell the author exactly how to
  convert: *"Move the file to `<skill-name>/SKILL.md` — create
  directory `<skill-name>/` and rename. See
  https://agentskills.io/specification#directory-structure."*
- **Q2 — `name` character class:** strict ASCII `[a-z0-9-]`
  with the combined regex
  `^[a-z0-9](?:[a-z0-9]|-(?!-))*[a-z0-9]$|^[a-z0-9]$` and
  length 1-64. Tie-breaks the spec's "unicode lowercase (`a-z`)"
  contradiction in favor of the parenthetical ASCII range,
  which matches every published example. Future Unicode
  support is opt-in, not default.
- **Q3 — Soft-warn hook severity:** warnings only; errors are
  silent inside `SkillSpec.from_file` and surface through
  `clauditor lint`. Mirrors the existing `derive_skill_name`
  behavior (warns on non-fatal, stays silent on fallback).
  Keeps stderr clean on the hot iteration path.
- **Q4 — `--strict` reach:** `clauditor lint --strict` only.
  Other commands stay soft-warn. No `--strict` on `grade` /
  `validate` / etc. in this ticket; a follow-up can extend if
  demand appears. An `eval.json` field is out of scope.
- **Q5 — Body token-budget check:** skip in this ticket. Line
  count check (`AGENTSKILLS_BODY_TOO_LONG`, warning, >500)
  lands; `AGENTSKILLS_BODY_TOKEN_BUDGET` becomes a follow-up
  issue (no tokenizer dependency in this PR).

---

---

## Architecture Review

### Ratings

| Area | Rating | Notes |
|---|---|---|
| Path-argument handling | concern | CLI paths ≠ config paths; use `resolve()` + `is_file()` only, no containment anchor |
| CLI surface (argparse, flags, exit codes) | pass | `--strict` safe; follow existing `--json` opt-in convention |
| Output format | pass | Plain text default, `--json` opt-in — matches `audit`, `validate`, `grade` |
| Warning prefix convention | pass | Use `"clauditor.conformance: <CODE>: <message>"` (distinct from `"clauditor.spec: ..."`) |
| Success-message shape | concern | Project prints one-liner on pass in every existing command; lint should match (not stay Unix-silent) |
| Warning dedup with `derive_skill_name` | **blocker** | On invalid `name:`, both helpers would emit stderr lines for the same root issue |
| Bundled-skill conformance state | **blocker** | `src/clauditor/skills/clauditor/SKILL.md` uses `argument-hint` + `disable-model-invocation`; `src/clauditor/skills/review-agentskills-spec/SKILL.md` uses `disable-model-invocation`. Neither key is in the agentskills.io spec → `AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY` would fire on every skill load once the soft-warn hook lands |
| Test structure (pure helper + I/O split) | pass | One class per rule category in `test_conformance.py`; CLI tests in dedicated `test_cli_lint.py`; hook tests extend `test_spec.py::TestFromFile` |
| `tests/test_bundled_skill.py` regression | concern | Existing `test_skill_md_uses_disable_model_invocation` pins the unknown key; will need update depending on Q6 answer |
| Coverage gate (80%) | pass | Six high-risk branches identified (YAML parse failure, bool/str YAML coercion, frontmatter edge cases, path-layout mismatch, whitespace-only values); each has a concrete test pattern |

### Concerns requiring explicit decisions

**Q6 (blocker) — Warning dedup:** When a skill has an invalid `name:` frontmatter value, `derive_skill_name` today emits *"clauditor.spec: frontmatter name '...' is not a valid skill identifier — using '...'"* AND the new soft-warn hook would emit *"clauditor.conformance: AGENTSKILLS_NAME_INVALID_CHARS: ..."*. Two messages, same root issue. Same problem for name-vs-parent-dir mismatch. How do we consolidate?

**Q7 (blocker) — Bundled-skill unknown keys:** `argument-hint` and `disable-model-invocation` are Claude Code slash-command frontmatter (documented by Anthropic for CLI skills), not agentskills.io. They would trigger `AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY` warnings for every user whose skill also uses them. How do we treat these?

**Q8 (concern) — Path validation style:** Confirm CLI-level path handling is `Path(arg).resolve()` + `is_file()` with no containment check. Accept absolute paths, follow symlinks, reject directories.

**Q9 (concern) — Success message:** Silent (Unix style, exit 0 only) or one-liner (`"Conformance check passed for <path>"`)?

**Q10 (concern) — `--json` support:** Add `lint --json` in this ticket (matches `audit`, `validate`, `grade`) or defer to follow-up?

---

## Refinement Log

### Decisions

- **DEC-001 — Pure module shape.** New `src/clauditor/conformance.py` exports `ConformanceIssue` dataclass
  (`code: str`, `severity: Literal["error","warning"]`, `message: str`) and
  `check_conformance(skill_md_text: str, skill_path: Path) -> list[ConformanceIssue]`. No I/O, no stderr, no
  LLM. Traces to `.claude/rules/pure-compute-vs-io-split.md`.
- **DEC-002 — CLI entry.** `src/clauditor/cli/lint.py` exports `add_parser(subparsers)` and `cmd_lint(args)`,
  registered in `cli/__init__.py` alongside the 14 existing commands. Non-LLM 0/1/2 exit taxonomy
  (`.claude/rules/llm-cli-exit-code-taxonomy.md`). Traces to Q&A scoping.
- **DEC-003 — Soft-warn hook.** `SkillSpec.from_file` calls `check_conformance` after reading the SKILL.md text
  and emits **only `severity="warning"` issues** to stderr in the form
  `"clauditor.conformance: <CODE>: <message>"`. Errors are silent at this layer; users see them via
  `clauditor lint`. Traces to Q3.
- **DEC-004 — `--strict` scope.** `--strict` is a flag on `clauditor lint` only. It promotes warnings to errors
  (exit 2 whenever any issue exists). No `--strict` added to `validate`, `grade`, `extract`, or other commands
  in this ticket. A future ticket can extend if users ask. Traces to Q4.
- **DEC-005 — Legacy layout handling.** `AGENTSKILLS_LAYOUT_LEGACY` fires for legacy single-file `<name>.md`
  skills as a **warning by default**, with an explanatory message that names the required migration
  (`mkdir <skill-name>/ && mv <name>.md <skill-name>/SKILL.md` and a link to the spec). `--strict` promotes to
  error. This preserves back-compat for existing clauditor users while nudging them toward the modern layout.
  Traces to Q1.
- **DEC-006 — `name` regex (strict ASCII).** The regex is
  `^[a-z0-9](?:[a-z0-9]|-(?!-))*[a-z0-9]$|^[a-z0-9]$` with overall length 1-64. Tie-breaks the spec's
  self-contradictory "unicode lowercase (`a-z`)" phrase in favor of ASCII, matching every published example.
  The existing `paths.py::SKILL_NAME_RE` stays unchanged (different contract — internal safety filter).
  `conformance.py` defines its own `AGENTSKILLS_NAME_RE` constant; the two are not shared. Traces to Q2, G1.
- **DEC-007 — Body checks in scope.** Line-count check lands in this ticket: `AGENTSKILLS_BODY_TOO_LONG`
  (warning) fires when the body (post-frontmatter) exceeds 500 lines. The `< 5000 tokens` recommendation
  (`AGENTSKILLS_BODY_TOKEN_BUDGET`) is **deferred to a follow-up issue** — it needs a tokenizer and would
  complicate this non-LLM command's dependency graph. Traces to Q5.
- **DEC-008 — Retire `derive_skill_name` warning emission.** `src/clauditor/paths.py::derive_skill_name`
  becomes purely `(name, None)` — returns no warnings. The two existing warnings (invalid-name fallback,
  frontmatter-vs-filesystem mismatch) are replaced by equivalent `check_conformance` codes
  (`AGENTSKILLS_NAME_INVALID_CHARS`, `AGENTSKILLS_NAME_PARENT_DIR_MISMATCH`) routed through the soft-warn
  hook. Single source of truth for frontmatter-name warnings. Aligns with
  `.claude/rules/skill-identity-from-frontmatter.md`'s "a future `--strict` mode could escalate" intent.
  Updates `tests/test_paths.py::TestDeriveSkillName` and `tests/test_spec.py::TestFromFile` assertions.
  Traces to Q6.
- **DEC-009 — `KNOWN_CLAUDE_CODE_EXTENSION_KEYS` allowlist.** `conformance.py` carries a
  `frozenset[str]` allowlist of frontmatter keys that agent hosts use but the agentskills.io spec does not
  define. Initial contents: `{"argument-hint", "disable-model-invocation"}`. Keys in this allowlist do NOT
  trigger `AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY`. The allowlist is documented in the `lint` docs as an
  explicit clauditor extension. The `/review-agentskills-spec` skill (#72) maintains the allowlist against
  Claude Code's published frontmatter documentation — see DEC-013. Traces to Q7.
- **DEC-010 — Path validation style.** `cmd_lint` uses `Path(args.skill_md).resolve()` followed by
  `is_file()`. Accepts absolute paths, follows symlinks, rejects directories. Does NOT apply the full
  `.claude/rules/path-validation.md` containment recipe (that rule is for config-loaded paths inside a
  spec dir; a CLI-provided path has different invariants). Matches the existing `validate`/`grade` pattern.
  Traces to Q8.
- **DEC-011 — Success-message shape.** On pass, `cmd_lint` prints a one-line summary to stdout:
  `"Conformance check passed: <resolved-path>"`. Matches the existing convention in `validate`, `grade`,
  `audit`. Traces to Q9.
- **DEC-012 — `--json` output.** `clauditor lint --json` emits a JSON object to stdout of shape
  `{"schema_version": 1, "skill_path": "<path>", "passed": bool, "issues": [{"code": ..., "severity": ...,
  "message": ...}]}`. `schema_version: 1` is the first key per
  `.claude/rules/json-schema-version.md`. Non-JSON mode prints human output. Traces to Q10.
- **DEC-013 — #72 scope extension.** Issue #72 (the `/review-agentskills-spec` bundled skill) is extended
  via comment to include an audit of Claude Code's published skill/command frontmatter documentation.
  The skill becomes the maintainer of `KNOWN_CLAUDE_CODE_EXTENSION_KEYS` by periodically diffing Claude
  Code's field set against the allowlist. This keeps `conformance.py` offline and pure while giving the
  allowlist a living update path. Comment URL recorded in session notes. Traces to Q7 + extension.
- **DEC-014 — Warning prefix convention.** All conformance messages (CLI stderr, soft-warn hook stderr)
  use the prefix `"clauditor.conformance: <CODE>: <message>"`. Distinct from the existing
  `"clauditor.spec: ..."` prefix used by `SkillSpec`. Disambiguates which subsystem emitted the message
  when a user sees interleaved stderr from multiple sources.

### Load-bearing message copy

- **`AGENTSKILLS_LAYOUT_LEGACY` (warning):** *"Legacy single-file skill layout `<filename>` is not in the
  agentskills.io specification, which requires a `<skill-name>/SKILL.md` directory layout. To migrate:
  `mkdir <skill-name>/ && mv <filename> <skill-name>/SKILL.md`. See
  https://agentskills.io/specification#directory-structure."*
- **`AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY` (warning):** *"Unknown frontmatter key `<key>`; the agentskills.io
  specification defines only: `name`, `description`, `license`, `compatibility`, `metadata`,
  `allowed-tools`. If this is an extension recognized by a specific agent host, consider opening an issue
  to add it to the clauditor allowlist."*
- **`AGENTSKILLS_NAME_PARENT_DIR_MISMATCH` (error):** *"Frontmatter `name: <fm-name>` does not match parent
  directory `<parent-dir>`; the agentskills.io specification requires these to match."*

### Deferred to follow-up issues

- `AGENTSKILLS_BODY_TOKEN_BUDGET` — 5000-token recommendation (DEC-007).
- `--strict` on commands beyond `lint` (DEC-004).
- `eval.json` field to default-strict for a specific skill (Q4 option C).
- `AGENTSKILLS_DESCRIPTION_MISSING_WHEN_CLAUSE` / `_MISSING_KEYWORDS` (SHOULD-level prose-quality checks — not
  mechanically verifiable without an LLM judge).

---

## Detailed Breakdown

### Story map (dependency DAG)

```
US-001 (pure conformance module)
    |
    +--> US-002 (retire derive_skill_name warnings)
    |       |
    +-------+--> US-003 (CLI lint, plain text)
    |       |       |
    |       |       +--> US-004 (--strict flag)
    |       |       +--> US-005 (--json output)
    |       |
    +-------+--> US-006 (soft-warn hook in SkillSpec.from_file)
    |                       |
    |                       +--> US-007 (bundled-skill conformance)
    |                                       |
    +---------------------------------------+--> US-008 (docs + CHANGELOG + cross-ref)
                                                            |
                                                            +--> US-QG (Quality Gate)
                                                                        |
                                                                        +--> US-PM (Patterns & Memory)
```

### US-001 — Pure `conformance.py` module with full rule set

**Description:** Create `src/clauditor/conformance.py` with the `ConformanceIssue` dataclass and the
`check_conformance(skill_md_text, skill_path) -> list[ConformanceIssue]` pure function implementing every
rule from the Discovery section plus `AGENTSKILLS_LAYOUT_LEGACY`. Includes the
`KNOWN_CLAUDE_CODE_EXTENSION_KEYS` allowlist and the `AGENTSKILLS_NAME_RE` constant.

**Traces to:** DEC-001, DEC-005, DEC-006, DEC-007, DEC-009, and all spec rules enumerated in the Discovery
section.

**Files:**
- `src/clauditor/conformance.py` (new; ~350-450 lines including docstrings)
- `tests/test_conformance.py` (new; ~400-600 lines, class-per-category)

**TDD:** Strong fit.
- Minimal-valid-skill fixture. Tests mutate it per rule.
- Classes: `TestNameValidation`, `TestDescriptionValidation`, `TestLicenseValidation`,
  `TestCompatibilityValidation`, `TestMetadataValidation`, `TestAllowedToolsValidation`,
  `TestFrontmatterStructure` (unknown keys + allowlist), `TestBodyChecks`, `TestLayoutChecks` (legacy +
  parent-dir mismatch), `TestYAMLTypeCoercion` (`name: true` bool guard, `metadata.version: 1.0` coercion).

**Acceptance criteria:**
- `ConformanceIssue` dataclass with `code`, `severity: Literal["error","warning"]`, `message`.
- `check_conformance` returns `list[ConformanceIssue]`, empty on valid input.
- Every rule from the Discovery table produces a uniquely-coded issue with the right severity.
- `_frontmatter.parse_frontmatter` is the only frontmatter parser used; no re-implementation.
- `KNOWN_CLAUDE_CODE_EXTENSION_KEYS` is a module-level `frozenset[str]` containing at least
  `argument-hint` and `disable-model-invocation`.
- Malformed YAML (`ValueError` from `parse_frontmatter`) surfaces as
  `AGENTSKILLS_FRONTMATTER_INVALID_YAML` (error), not an uncaught exception.
- No I/O: `grep -n "open(\|read_text\|print(\|sys.stderr\|sys.stdout" src/clauditor/conformance.py`
  returns nothing.
- Coverage ≥85% on the new module (headroom for the 80% gate).
- `uv run ruff check src/clauditor/conformance.py tests/test_conformance.py` passes.

**Done when:** Tests pass, coverage ≥85%, ruff clean.

---

### US-002 — Retire `derive_skill_name` warning emission

**Description:** `src/clauditor/paths.py::derive_skill_name` currently returns `(name, warning | None)`. Per
DEC-008, drop the warning branch: always return `(name, None)`. The two warnings it used to emit are now
produced by `check_conformance` via US-006's hook. Simplify the signature to `-> str` (or keep the tuple
with `None` always for caller compat — prefer the simpler signature change).

**Traces to:** DEC-008.

**Files:**
- `src/clauditor/paths.py` — remove lines ~136-149 (warning construction); simplify return type.
- `src/clauditor/spec.py::SkillSpec.from_file` — remove the `if warning is not None: print(...)` branch.
- `src/clauditor/cli/init.py::cmd_init` — remove the same pattern.
- `tests/test_paths.py::TestDeriveSkillName` — remove warning-return assertions; tests now assert on name
  only.
- `tests/test_spec.py::TestFromFile` — remove the stderr-warning assertions tied to `derive_skill_name`
  (they move to US-004/US-006 and will assert on the `check_conformance` path instead).

**Depends on:** US-001 (US-006 will back-fill the behavior before US-002's removal can land without a
regression gap — but US-002 lands ahead of US-006 in code order; the gap is closed when US-006 ships. This
ticket accepts a brief regression window within the PR; it is closed before merge).

**TDD:** Light — remove existing assertions, add assertion that return is always `(name, None)` (or
simplified `name`).

**Acceptance criteria:**
- `derive_skill_name` has no stderr emission, no warning construction, no `warning | None` in its return
  type (or returns `(name, None)` always, caller's choice).
- `SkillSpec.from_file` no longer has a `if warning is not None: print(...)` branch tied to
  `derive_skill_name`'s output.
- `cmd_init` mirror: same branch removed.
- All existing tests for `derive_skill_name` continue to pass with updated assertions.
- `uv run ruff check src/ tests/` passes.

**Done when:** The grep `grep -rn "clauditor.spec: frontmatter name" src/` returns zero matches in
source, and tests pass.

---

### US-003 — `clauditor lint` CLI command (plain text output)

**Description:** Create `src/clauditor/cli/lint.py` with `add_parser(subparsers)` and `cmd_lint(args)`.
Register in `src/clauditor/cli/__init__.py`. Positional path argument; path resolution per DEC-010. Human
text output per DEC-011. Exit 0 on pass; exit 1 on load/parse failure (file not found, unreadable, malformed
YAML → `AGENTSKILLS_FRONTMATTER_INVALID_YAML`); exit 2 on any conformance issue. `--strict` and `--json`
deferred to US-004 and US-005.

**Traces to:** DEC-002, DEC-010, DEC-011, DEC-014.

**Files:**
- `src/clauditor/cli/lint.py` (new; ~100-150 lines)
- `src/clauditor/cli/__init__.py` (modify; 2 imports, 1 `add_parser` call, 1 dispatch elif)
- `tests/test_cli_lint.py` (new; ~200-300 lines, `TestCmdLint` class)
- `tests/test_cli.py::_MISSING_SKILL_FILE_COMMANDS` — add `["lint", "nonexistent.md"]` parametrized entry
  per existing US-002 pattern

**Depends on:** US-001.

**TDD:** Fit.
- Test "pass" path: minimal-valid skill → exit 0 + stdout success line.
- Test "error" path: skill with an error issue → exit 2 + stderr issue lines.
- Test "warning-only" path: skill with warnings only, no `--strict` → exit 0 + stderr warning lines (happy
  path with warnings).
- Test path-validation failures: absolute path to nowhere → exit 1; path is a directory → exit 1.
- Test stdout prefix: human mode emits `"Conformance check passed: <path>"` on pass; issues go to stderr as
  `"clauditor.conformance: <CODE>: <message>"`.

**Acceptance criteria:**
- `clauditor lint <path>` resolves the path, reads the file, calls `check_conformance`, renders issues.
- Exit codes: 0 (pass), 1 (load/parse failure or bad path), 2 (one or more conformance issues).
- Success message to stdout matches DEC-011 exactly.
- Issue messages to stderr match DEC-014 prefix format exactly.
- `cli/__init__.py` registration mirrors the existing subcommand pattern.
- `tests/test_cli.py::test_command_missing_skill_file_exits_2` (or its rename from test_cli.py) covers
  `lint` in its parametrized list.
- `uv run ruff check src/ tests/` passes.
- Coverage ≥85% on `cli/lint.py`.

**Done when:** `clauditor lint src/clauditor/skills/clauditor/SKILL.md` executes with the correct exit
code (depends on US-007's outcome; initially will likely exit 2 on the unknown-key warnings before
US-007 lands).

---

### US-004 — `--strict` flag on `lint`

**Description:** Add `--strict` flag to `clauditor lint`. When set, any warning produces exit 2 (same exit
as errors). When unset, warnings do not affect exit code (still render to stderr).

**Traces to:** DEC-004.

**Files:**
- `src/clauditor/cli/lint.py` — add `--strict` via `add_parser`; branch the exit-code logic.
- `tests/test_cli_lint.py` — parametrized `(strict: bool, severity: str, expected_rc: int)` matrix per
  Review C's sample.

**Depends on:** US-003.

**TDD:** Fit — the parametrized matrix is a natural TDD shape.

**Acceptance criteria:**
- `lint --strict <path>` exits 2 on warnings that would otherwise exit 0.
- `lint <path>` (no `--strict`) exits 0 on warning-only input.
- `lint --strict` does NOT change the rendering of warnings (they still emit to stderr identically).
- Help text (`clauditor lint --help`) describes the flag's behavior in one sentence.

**Done when:** All six cells of the parametrized test matrix pass.

---

### US-005 — `--json` output on `lint`

**Description:** Add `--json` flag to `clauditor lint`. When set, emit a JSON object to stdout of shape
`{"schema_version": 1, "skill_path": "<resolved-path>", "passed": bool, "issues": [{"code": ...,
"severity": ..., "message": ...}, ...]}`. `"passed"` is `true` iff no errors AND (with `--strict`) no
warnings. Suppresses the human text stdout/stderr.

**Traces to:** DEC-012.

**Files:**
- `src/clauditor/cli/lint.py` — add `--json` flag; branch output rendering.
- `tests/test_cli_lint.py` — JSON-mode tests (`json.loads(captured.out)` assertions).

**Depends on:** US-003.

**TDD:** Fit.

**Acceptance criteria:**
- `lint --json <path>` emits a single JSON object to stdout; stderr is empty.
- `schema_version: 1` is the first key in the payload (per `.claude/rules/json-schema-version.md`).
- `issues[]` entries include all three `ConformanceIssue` fields.
- `passed` is derived correctly under both `--strict` and non-strict modes.
- Exit codes are identical to the human-output path (DEC-012 does not change exit codes).
- `json.loads(captured.out)["issues"][0]["code"].startswith("AGENTSKILLS_")` passes on an invalid skill.
- Help text documents `--json` in one sentence.

**Done when:** JSON output parses cleanly and contains the expected keys + types in a representative
fixture.

---

### US-006 — Soft-warn hook in `SkillSpec.from_file`

**Description:** After reading the SKILL.md text in `SkillSpec.from_file`, call `check_conformance(text,
skill_path)` and emit **only `severity="warning"` issues** to stderr with the
`"clauditor.conformance: <CODE>: <message>"` prefix. Errors are silent at this seam; they surface when
the user runs `clauditor lint`. The hook never raises; spec loading continues regardless.

**Traces to:** DEC-003, DEC-014.

**Files:**
- `src/clauditor/spec.py::SkillSpec.from_file` — add the hook after `derive_skill_name` call (~5-10 lines).
- `tests/test_spec.py::TestFromFile` — add tests for the hook: emits on warnings, silent on errors, never
  blocks.

**Depends on:** US-001, US-002.

**TDD:** Fit.
- Test: skill with a `warning`-severity issue (e.g. body >500 lines) → `capsys.readouterr().err` contains
  the prefix + code.
- Test: skill with an `error`-severity issue (e.g. missing `name:`) → no stderr from the hook (errors are
  silent here). Spec still loads (with whatever fallback `derive_skill_name` produces).
- Test: skill with both warnings and errors → only warnings emit to stderr.
- Test: hook does NOT raise on malformed YAML (the `AGENTSKILLS_FRONTMATTER_INVALID_YAML` error doesn't
  reach stderr here, so it cannot block from_file).

**Acceptance criteria:**
- The hook is ~10 lines, one loop over `check_conformance` output filtered on severity.
- Zero new exceptions propagate out of `from_file` from the hook.
- All existing `TestFromFile` tests continue to pass.
- New tests cover the four cases above.

**Done when:** `tests/test_spec.py` passes, and a `grep "clauditor.conformance:" src/clauditor/spec.py`
finds exactly one occurrence (the prefix string).

---

### US-007 — Bundled-skill conformance audit + regression test

**Description:** Verify both shipped bundled skills (`src/clauditor/skills/clauditor/SKILL.md` and
`src/clauditor/skills/review-agentskills-spec/SKILL.md`) pass `check_conformance` with **zero errors and
zero warnings outside the `KNOWN_CLAUDE_CODE_EXTENSION_KEYS` allowlist**. If any drift surfaces, either
add the key to the allowlist (with a comment referencing Claude Code's docs per DEC-013) or fix the
frontmatter. Add `TestBundledSkillConformance` regression class to `tests/test_bundled_skill.py`.

**Traces to:** DEC-009, DEC-013, `.claude/rules/bundled-skill-docs-sync.md`.

**Files:**
- `src/clauditor/skills/clauditor/SKILL.md` — potential frontmatter adjustments (expect none; `argument-hint`
  and `disable-model-invocation` should already be allowlisted).
- `src/clauditor/skills/review-agentskills-spec/SKILL.md` — same.
- `src/clauditor/conformance.py` — the `KNOWN_CLAUDE_CODE_EXTENSION_KEYS` constant may need additions after
  the audit (discovered at impl time).
- `tests/test_bundled_skill.py` — add `TestBundledSkillConformance` class asserting `check_conformance`
  returns `[]` (or only allowlist-matched issues) for both skills.
- `tests/test_bundled_skill.py::test_skill_md_uses_disable_model_invocation` — no change; the allowlist
  preserves this field.

**Depends on:** US-001, US-006.

**TDD:** Fit — write the regression assertion first, it drives any allowlist additions.

**Acceptance criteria:**
- Running `check_conformance(skill_md.read_text(), skill_md)` on each bundled skill returns `[]` (empty).
  No errors, no warnings, no allowlist-bypass.
- `TestBundledSkillConformance` class in `tests/test_bundled_skill.py` asserts this for both skills in
  two methods (one per skill).
- If the audit surfaced new Claude Code extension keys, they are added to
  `KNOWN_CLAUDE_CODE_EXTENSION_KEYS` with a comment line citing the source (Anthropic docs URL when
  known; "Claude Code extension — see #72's audit" otherwise).
- The soft-warn hook is validated end-to-end:
  `capsys.readouterr().err` on `SkillSpec.from_file(<bundled-SKILL>)` is empty.

**Done when:** The regression test passes on both bundled skills, and manual `clauditor lint
src/clauditor/skills/clauditor/SKILL.md` exits 0 with the "Conformance check passed" line.

---

### US-008 — Documentation: cli-reference, README, CHANGELOG, cross-ref

**Description:** Per the ticket's Section 5 and `.claude/rules/readme-promotion-recipe.md`:
1. `docs/cli-reference.md` — new `## lint` section (required inputs, flags table, examples, exit codes);
   add `clauditor lint <path>` to the quick-reference table at the top.
2. `README.md` — one-line addition to the "CLI Reference" subcommand list and a D2-lean teaser under the
   "Beyond the spec" bullet list in the agentskills.io alignment block (if one exists; otherwise skip).
3. `CHANGELOG.md` — `### Added` entry under `## [Unreleased]` covering the `lint` command, soft-warn hook,
   `--strict`, `--json`, and the `KNOWN_CLAUDE_CODE_EXTENSION_KEYS` allowlist.
4. `CONTRIBUTING.md` — add `clauditor lint src/clauditor/skills/clauditor/SKILL.md` as a pre-release
   dogfood gate (maintainer convention: bundled skill must pass lint before tag).
5. **Cross-reference `/review-agentskills-spec`** in the new lint section so maintainers and users don't
   conflate the two. Add an inverse cross-reference in the bundled skill's documentation.
6. `docs/pytest-plugin.md` — one-line "lint is a standalone CLI, not exposed via pytest" pointer.
7. `docs/eval-spec-reference.md` — no changes (no new spec fields).
8. `docs/skill-usage.md` — no changes (bundled `/clauditor` skill workflow does NOT gain a lint step in this
   ticket; if it did, `.claude/rules/bundled-skill-docs-sync.md`'s triangle would apply).
9. Regression test per `.claude/rules/bundled-skill-docs-sync.md`: add a
   `tests/test_docs_examples.py` grep assertion for `"clauditor lint"` in `docs/cli-reference.md` so a
   future prose cleanup cannot silently drop the command.

**Traces to:** DEC-002 (documented as cli-reference entry),
`.claude/rules/readme-promotion-recipe.md`, `.claude/rules/bundled-skill-docs-sync.md`.

**Files:**
- `docs/cli-reference.md` (modify; add `## lint` section, update quick-reference table)
- `README.md` (modify; one-line addition)
- `CHANGELOG.md` (modify; new `### Added` entry)
- `CONTRIBUTING.md` (modify; add `lint` to pre-release gate list)
- `docs/pytest-plugin.md` (modify; one-line pointer)
- `tests/test_docs_examples.py` (modify or create; grep regression assertion)
- `src/clauditor/skills/review-agentskills-spec/SKILL.md` (modify; add inverse cross-ref to `lint`)

**Depends on:** US-007 (docs describe the final state including the allowlist).

**TDD:** Light — the grep-regression is a one-line prose-presence assertion.

**Acceptance criteria:**
- `docs/cli-reference.md` has a `## lint` section with Required inputs, Flags (table), Examples, Exit
  codes. Quick-reference table lists `clauditor lint <path>`.
- README references `clauditor lint` in the CLI Reference list.
- CHANGELOG's `## [Unreleased]` has a new `### Added` entry covering all user-visible changes.
- `tests/test_docs_examples.py` has a grep for `"clauditor lint"` in `docs/cli-reference.md`.
- Cross-references between `lint` docs and `/review-agentskills-spec` skill docs are in place.

**Done when:** All doc files updated, grep-regression passes, maintainer-dogfood step added to
CONTRIBUTING.

---

### US-QG — Quality Gate

**Description:** Run the standard clauditor quality gate across the full changeset of US-001 through
US-008:
1. `uv run ruff check src/ tests/` passes with zero findings.
2. `uv run pytest --cov=clauditor --cov-report=term-missing` passes with ≥80% total coverage.
3. Code reviewer (general-purpose agent with review prompt) runs 4 times over the changeset, fixing every
   real issue found. Passes 2-4 reference prior passes' findings.
4. CodeRabbit review if available on the branch's PR; fix every real issue or justify false positives.
5. Final re-run of `ruff` + `pytest` after each fix pass to confirm no regressions.

**Traces to:** `.claude/rules/pytester-inprocess-coverage-hazard.md` (watch for segfault risk if any new
pytester tests land), project convention.

**Files:** None created; may modify source files during fixes.

**Depends on:** US-001 through US-008 all complete.

**Acceptance criteria:**
- `uv run ruff check src/ tests/` exit 0.
- `uv run pytest --cov=clauditor --cov-fail-under=80` exit 0.
- Four reviewer passes completed; each pass's findings addressed.
- Manual validation: `clauditor lint src/clauditor/skills/clauditor/SKILL.md` exits 0 with the
  success line.
- Manual validation: `clauditor lint src/clauditor/skills/clauditor/SKILL.md --json | jq .passed`
  returns `true`.
- Manual validation: `clauditor validate src/clauditor/skills/clauditor/SKILL.md` stderr is empty
  (no conformance warnings from soft-warn hook, confirming the bundled skill is conformant).

**Done when:** All acceptance criteria met and CI green.

---

### US-PM — Patterns & Memory

**Description:** Capture any novel patterns from this ticket into `.claude/rules/` or memory. Candidates:
1. **New rule anchor for `--strict` flag shape** — if `--strict` shape generalizes beyond `lint`, add a
   rule doc. Likely not needed for a single-command flag; skip if the shape is local.
2. **New rule anchor for `ConformanceIssue`-style issue lists** — the `code / severity / message` shape
   is reusable for future static-check features (rubric conformance, trigger-file conformance, etc.). If
   it generalizes, add a rule. Write the rule only if a second caller appears; otherwise document the
   shape in `conformance.py`'s module docstring as the canonical reference.
3. **Update `.claude/rules/skill-identity-from-frontmatter.md`** — with DEC-008, `derive_skill_name` is no
   longer the emission point for frontmatter-name warnings. Update the rule's "Why each piece matters"
   section to point at `conformance.py` as the new single source for name validation messaging.
4. **Update `.claude/rules/bundled-skill-docs-sync.md`** — if US-008 added the bundled-skill cross-ref
   pattern for maintainer-audience docs, extend the rule's "Canonical implementation" section.

**Traces to:** Priority 99 (always last).

**Files:** Updates to `.claude/rules/*.md`; possible new `.claude/rules/static-conformance-issue-list.md`.

**Depends on:** US-QG.

**Acceptance criteria:**
- Any genuinely-novel shape from this ticket is captured in a rule doc or memory entry.
- Pre-existing rules whose canonical-implementation pointers became stale (e.g. `skill-identity-from-
  frontmatter.md` after US-002) are updated.
- No speculative rules — skip anchors that would need a second caller to be useful.

**Done when:** Rules grepped for stale references, updated where needed; memory appended with
feature-relevant insight if any.

---

## Story summary

| ID | Title | Files | Depends on | TDD |
|---|---|---|---|---|
| US-001 | Pure `conformance.py` module | `conformance.py`, `test_conformance.py` | — | Yes |
| US-002 | Retire `derive_skill_name` warnings | `paths.py`, `spec.py`, `cli/init.py`, tests | US-001 | Light |
| US-003 | CLI `lint` (plain text) | `cli/lint.py`, `cli/__init__.py`, `test_cli_lint.py`, `test_cli.py` | US-001 | Yes |
| US-004 | `--strict` flag | `cli/lint.py`, `test_cli_lint.py` | US-003 | Yes |
| US-005 | `--json` output | `cli/lint.py`, `test_cli_lint.py` | US-003 | Yes |
| US-006 | Soft-warn hook | `spec.py`, `test_spec.py` | US-001, US-002 | Yes |
| US-007 | Bundled-skill conformance | bundled SKILL.md files, `conformance.py`, `test_bundled_skill.py` | US-001, US-006 | Yes |
| US-008 | Docs + CHANGELOG + cross-ref | docs/*, README.md, CHANGELOG.md, CONTRIBUTING.md, `test_docs_examples.py` | US-007 | Light |
| US-QG | Quality Gate | (no new files) | US-001..US-008 | No |
| US-PM | Patterns & Memory | `.claude/rules/*` | US-QG | No |

---

---

## Beads Manifest

- **Epic:** `clauditor-zsf` — #71: Agentskills.io spec conformance check for SKILL.md
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/71-agentskills-lint`
- **Branch:** `feature/71-agentskills-lint`
- **PR:** https://github.com/wjduenow/clauditor/pull/74

| Bead | Story | Depends on | Priority |
|---|---|---|---|
| `clauditor-zsf.1` | US-001 — Pure `conformance.py` module | — | P2 |
| `clauditor-zsf.2` | US-002 — Retire `derive_skill_name` warnings | `.1` | P2 |
| `clauditor-zsf.3` | US-003 — CLI `lint` (plain text) | `.1` | P2 |
| `clauditor-zsf.4` | US-004 — `--strict` flag | `.3` | P2 |
| `clauditor-zsf.5` | US-005 — `--json` output | `.3` | P2 |
| `clauditor-zsf.6` | US-006 — Soft-warn hook in `SkillSpec.from_file` | `.1`, `.2` | P2 |
| `clauditor-zsf.7` | US-007 — Bundled-skill conformance + regression test | `.1`, `.6` | P2 |
| `clauditor-zsf.8` | US-008 — Docs + CHANGELOG + cross-ref | `.7` | P2 |
| `clauditor-zsf.9` | Quality Gate | `.4`, `.5`, `.8` | P2 |
| `clauditor-zsf.10` | Patterns & Memory | `.9` | P4 |

Ready now: `clauditor-zsf.1`. Everything else blocked on the DAG above.

---

## Session Notes

**2026-04-21 — Discovery complete.** Three parallel subagents
ran: codebase scout, convention checker, spec extractor. The
spec extractor's live fetch surfaced three ticket-vs-spec
mismatches (500-line limit is in spec, `< 5000 tokens` limit
is in spec, parent-dir-match is unqualified) and 10 gaps
requiring explicit decisions. User answered all 5 scoping
questions (Q1=D+explanatory, Q2=A, Q3=A, Q4=A, Q5=B). Moving
to architecture review — path handling, CLI surface, warning
dedup with existing `derive_skill_name`, testing shape.

**2026-04-21 — Architecture review complete.** Three parallel
subagents: security/CLI surface, warning-dedup/bundled-skill
compliance, testing strategy. Two blockers surfaced: (1) the
soft-warn hook would emit duplicate stderr lines with existing
`derive_skill_name` warnings for the same root issue; (2) both
shipped bundled skills use Claude Code frontmatter extensions
(`argument-hint`, `disable-model-invocation`) not in the
agentskills.io spec — they would trigger
`AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY` on every load. Three
smaller concerns on path handling style, success-message
shape, and `--json` scope. Moving to refinement to resolve.

**2026-04-21 — Devolved to beads.** Epic `clauditor-zsf` + 10
tasks `clauditor-zsf.1` through `clauditor-zsf.10`.
Dependency graph matches the Story summary DAG:
US-001 is the sole ready task; everything else gates on it or
its downstream. Ralph can begin on US-001 via
`bd update clauditor-zsf.1 --claim && bd show clauditor-zsf.1`.

**2026-04-21 — Refinement complete.** User resolved Q6=A
(retire `derive_skill_name` warnings; consolidate into
`check_conformance`), Q7=A (define
`KNOWN_CLAUDE_CODE_EXTENSION_KEYS` allowlist) plus an
extension to issue #72's scope to keep the allowlist
maintained against Claude Code's published frontmatter docs,
Q8=A (simple `resolve()` + `is_file()` CLI path validation),
Q9=B (one-liner success message matching project
convention), Q10=A (ship `--json` in this ticket).
DEC-001 through DEC-014 recorded.
Comment added to #72:
https://github.com/wjduenow/clauditor/issues/72#issuecomment-4289971205.
Ten stories drafted: US-001 (pure module), US-002 (retire
old warnings), US-003 (CLI), US-004 (`--strict`), US-005
(`--json`), US-006 (hook), US-007 (bundled-skill conformance),
US-008 (docs), US-QG (Quality Gate), US-PM (Patterns).
