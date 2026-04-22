# Rule: Derive skill identity from frontmatter first, filesystem second, lenient on failure

When a feature needs to derive a skill's identity (its `skill_name`)
from a `SKILL.md` file, consult the YAML frontmatter `name:` field
first, validate it against `SKILL_NAME_RE`, and fall back to a
**layout-aware** filesystem-derived name when frontmatter is absent or
invalid. The helper is pure — it takes the already-loaded Markdown
text, emits no stderr, and returns the resolved `str` name. Malformed
frontmatter and regex failures are lenient: fall back, keep going. A
typo in YAML should never make a skill uncallable. Warning emission
for invalid-name fallback and frontmatter-vs-filesystem mismatch is
the responsibility of `clauditor.conformance.check_conformance`
(routed through the `SkillSpec.from_file` soft-warn hook, DEC-003 /
DEC-008 / DEC-014 of `plans/super/71-agentskills-lint.md`) — the
identity-derivation helper itself emits no diagnostics.

## The pattern

```python
# src/clauditor/paths.py — pure, no I/O
SKILL_NAME_RE: str = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"


def _filesystem_name(skill_path: Path) -> str:
    """Layout-aware filesystem fallback."""
    if skill_path.name == "SKILL.md":
        return skill_path.parent.name   # modern: <dir>/SKILL.md
    return skill_path.stem              # legacy: <name>.md


def derive_skill_name(skill_path: Path, skill_md_text: str) -> str:
    fs_name = _filesystem_name(skill_path)

    from clauditor._frontmatter import parse_frontmatter
    try:
        parsed, _body = parse_frontmatter(skill_md_text)
    except ValueError:
        return fs_name  # malformed frontmatter → treat as absent

    if not isinstance(parsed, dict) or "name" not in parsed:
        return fs_name  # no name: key → silent fallback

    fm_name = parsed["name"]
    if not isinstance(fm_name, str) or re.fullmatch(SKILL_NAME_RE, fm_name) is None:
        return fs_name  # invalid frontmatter name → silent fallback;
                        # conformance.check_conformance emits the
                        # AGENTSKILLS_NAME_INVALID_CHARS error, which
                        # surfaces via ``clauditor lint`` (not through
                        # the soft-warn hook — hook filters out errors).

    # Disagreement (fm_name != fs_name) returns the frontmatter value
    # unchanged. The conformance checker emits the
    # AGENTSKILLS_NAME_PARENT_DIR_MISMATCH error separately; this
    # helper stays silent. That error surfaces via ``clauditor lint``
    # only, per DEC-003's warnings-only soft-warn-hook contract.
    return fm_name
```

At the call site, the I/O layer owns `read_text` and — via the
conformance soft-warn hook — stderr for the **warning** severity
only. Name-related issues (`AGENTSKILLS_NAME_INVALID_CHARS`,
`AGENTSKILLS_NAME_PARENT_DIR_MISMATCH`) are emitted by
`check_conformance` with `severity="error"`, so the hook filters
them out — they surface via `clauditor lint` (exit 2) instead, not
during routine skill loads. Non-name warnings (body length,
experimental `allowed-tools`, legacy layout) DO surface here:

```python
# src/clauditor/spec.py::SkillSpec.from_file
text = skill_path.read_text(encoding="utf-8")
skill_name = derive_skill_name(skill_path, text)

# Soft-warn hook: emit conformance WARNINGS (severity="warning") —
# e.g. AGENTSKILLS_BODY_TOO_LONG, AGENTSKILLS_LAYOUT_LEGACY.
# Errors like AGENTSKILLS_NAME_INVALID_CHARS /
# AGENTSKILLS_NAME_PARENT_DIR_MISMATCH are filtered out here and
# surface only via ``clauditor lint``. Single seam formatted by
# ``conformance.format_issue_line``. ``check_conformance`` never raises.
for issue in check_conformance(text, skill_path):
    if issue.severity == "warning":
        print(format_issue_line(issue), file=sys.stderr)
return cls(..., skill_name_override=skill_name)
```

## Why each piece matters

- **Frontmatter `name:` wins when present and valid**: matches
  Anthropic's documented shape for skills shared via plugins and
  agentskills.io, where `<name>/SKILL.md` is the canonical on-disk
  layout and the frontmatter `name:` is the authoritative identity.
  The path is secondary (a skill can be symlinked, renamed, or
  packaged under a different directory without changing its identity).
- **Layout-aware filesystem fallback**: uniform `skill_path.stem`
  returns the literal string `"SKILL"` for the modern `<dir>/SKILL.md`
  layout. Branching on `skill_path.name == "SKILL.md"` is the
  minimum-viable layout classifier. Legacy `.claude/commands/<name>.md`
  and modern `.claude/skills/<name>/SKILL.md` are both first-class.
- **Regex validation against `SKILL_NAME_RE`**: the derived name flows
  into `f"/{skill_name}"` which becomes the Claude Code slash-command
  argument, and may also be interpolated into filesystem path segments
  (capture output files, iteration dir names). The regex
  (`^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`) blocks command injection,
  argument injection, and path-traversal via hostile `name:` values
  like `"foo; rm -rf /"` or `"../../../etc/passwd"`. `SKILL_NAME_RE`
  lives in `paths.py` as a shared constant; every caller uses
  `re.fullmatch` (not `re.match`) so trailing newlines don't pass.
  Note that `SKILL_NAME_RE` is clauditor's internal safety filter and
  is **intentionally different** from
  `conformance.AGENTSKILLS_NAME_RE` (strict ASCII lowercase per
  DEC-006 of `plans/super/71-agentskills-lint.md`); the two regexes
  have different contracts (internal safety vs external publish
  conformance) and must not be merged.
- **Lenient on regex failure, not strict**: a malformed frontmatter
  value shouldn't make the skill uncallable — the filesystem fallback
  is the already-validated path segment (the parent dir / stem went
  through the OS). Fall back, keep going. The corresponding
  `AGENTSKILLS_NAME_INVALID_CHARS` (or
  `AGENTSKILLS_NAME_PARENT_DIR_MISMATCH`) is emitted by
  `check_conformance` as an **error**; it surfaces via
  `clauditor lint` (exit 2) rather than the soft-warn hook (which
  filters to warnings only per DEC-003), so the author notices when
  they actively lint their skill without being blocked during routine
  loads.
- **Malformed frontmatter treated as absent**: `parse_frontmatter`
  raises `ValueError` on structural errors (missing closing `---`,
  empty key, etc.). A hard failure would be hostile to authors
  iterating on a skill; silent fallback preserves load-bearing
  behavior. The conformance checker surfaces the
  `AGENTSKILLS_FRONTMATTER_INVALID_YAML` error via `clauditor lint`
  (exit 1), and `--strict` on `lint` can escalate additional
  warnings per DEC-004 of `plans/super/71-agentskills-lint.md`.
- **Pure `str` return, no warning channel**: the helper emits no
  stderr, touches no disk, and has no side-channel. Callers that want
  diagnostic output consult `conformance.check_conformance` through
  the `SkillSpec.from_file` soft-warn hook (a single seam per DEC-014
  of `plans/super/71-agentskills-lint.md`). Satisfies
  `.claude/rules/pure-compute-vs-io-split.md` — tests can assert on
  the returned name without `capsys`, and the integration tests that
  use `capsys` are a separate class that drive the conformance hook,
  not the identity helper.
- **Disagreement returns the frontmatter name; conformance surfaces
  the mismatch**: when `fm_name != fs_name`, frontmatter wins
  (identity resolution) and `check_conformance` emits
  `AGENTSKILLS_NAME_PARENT_DIR_MISMATCH` as an **error** (not a
  warning). Because the soft-warn hook filters to warnings per
  DEC-003, this error surfaces via `clauditor lint` (exit 2), not
  during routine `SkillSpec.from_file` loads. This future-proofs
  against accidental renames (someone moves `<dir>/SKILL.md` to
  `<other-dir>/SKILL.md` but forgets to update the frontmatter): the
  skill still loads under its frontmatter-declared identity, and the
  error alerts the author the next time they run `clauditor lint` on
  the skill before publishing.
- **`SKILL_NAME_RE` is a shared constant, not inlined**: two callers
  currently validate against it (`paths.py::derive_skill_name` and
  `propose_eval.py::_derive_skill_name_from_path_or_frontmatter`). A
  third caller — e.g. a future rubric proposer, a plugin uploader, or
  a registry client — should import the constant, not copy the regex.
  A drift between two inlined regexes is a silent security footgun.
- **Single seam for conformance-warning emission**: post-#71, the
  only place a user sees `"clauditor.conformance: ..."` stderr lines
  during a routine spec load is the `SkillSpec.from_file` soft-warn
  hook, which calls `check_conformance` and filters to
  `severity="warning"`. The CLI `clauditor lint` command calls the
  same pure helper but also surfaces `severity="error"` issues (exit
  2). Both paths format their lines via one `format_issue_line`
  helper — do NOT hand-roll the prefix at call sites.

## What NOT to do

- Do NOT hard-fail `from_file` when frontmatter is malformed or `name:`
  fails the regex. The fallback path is the minimum-viable identity;
  hard-failing is hostile to authors and masks the real fix site.
- Do NOT emit stderr from inside `derive_skill_name`. The helper is
  pure; diagnostics belong to the conformance layer (see
  `.claude/rules/pure-compute-vs-io-split.md`). Re-adding a
  `warning: str | None` return would re-introduce the dual-emission
  bug DEC-008 of `plans/super/71-agentskills-lint.md` specifically
  retired (two stderr lines for one root cause).
- Do NOT return a uniform `skill_path.stem` for both layouts. That is
  the bug this rule codifies around — `stem` returns `"SKILL"` for the
  modern layout.
- Do NOT omit the `isinstance(fm_name, str)` guard. `parsed.get("name")`
  can return `None` or a non-string depending on what the YAML subset
  parsed; `re.fullmatch` on a non-string raises `TypeError`, which
  would escape.
- Do NOT loosen `SKILL_NAME_RE` without rewriting every interpolation
  site to escape the name. The regex is the single anchor keeping
  `f"/{skill_name}"` and any `skill_name`-as-path-segment safe.
- Do NOT re-use `SKILL_NAME_RE` for agentskills.io conformance
  checks. The internal safety regex allows uppercase and underscores
  (for back-compat with Claude Code slash-command names); the
  conformance regex does not. See
  `src/clauditor/conformance.py::AGENTSKILLS_NAME_RE` and DEC-006 of
  `plans/super/71-agentskills-lint.md`.

## Canonical implementation

- Shared regex: `src/clauditor/paths.py::SKILL_NAME_RE`.
- Pure helpers: `src/clauditor/paths.py::_filesystem_name` +
  `derive_skill_name` — the latter returns `str` (no tuple, no
  `warning` channel) per DEC-008 of
  `plans/super/71-agentskills-lint.md`.
- I/O caller: `src/clauditor/spec.py::SkillSpec.from_file` — reads
  the file, calls the pure helper for identity, and separately
  invokes `clauditor.conformance.check_conformance(...)` + filters
  `severity == "warning"` through `format_issue_line` (the
  `"clauditor.conformance: <CODE>: <message>"` single seam per
  DEC-014). Invalid-name and parent-dir-mismatch stderr lines
  originate from the conformance module, not the identity helper.
- Back-compat shape: `SkillSpec.__init__` accepts
  `skill_name_override: str | None = None` and falls back to a
  layout-aware no-I/O derivation when the override is `None`
  (preserves the direct-constructor path used by
  `tests/test_quality_grader.py`'s
  `SkillSpec(Path("dummy.md"), ...)` fixture).
- Second caller: `src/clauditor/cli/init.py::cmd_init` — reads the
  file, calls `derive_skill_name`, uses the name in the starter
  eval's `skill_name` and `description` fields. No warning branch
  (DEC-008 retired it).
- Conformance companion (single source of conformance diagnostics):
  `src/clauditor/conformance.py::check_conformance` — the pure
  function producing `ConformanceIssue` records (including
  `AGENTSKILLS_NAME_INVALID_CHARS`,
  `AGENTSKILLS_NAME_PARENT_DIR_MISMATCH`,
  `AGENTSKILLS_NAME_LEADING_HYPHEN`, etc.); and
  `format_issue_line(issue)` — the single renderer the hook and the
  `clauditor lint` CLI both call.
- Tests: `tests/test_paths.py::TestDeriveSkillName` (pure-helper
  cases, no `tmp_path`, assert on the `str` return only) and
  `tests/test_spec.py::TestFromFile` (integration cases covering
  both layouts + `capsys` for conformance-hook warning emission).
- Regression test: `tests/test_bundled_skill.py::TestBundledSkillViaSpec`
  loads the project's own modern-layout bundled `SKILL.md` through
  `SkillSpec.from_file` — a real-file self-validation — and
  `tests/test_bundled_skill.py::TestBundledSkillConformance`
  asserts `check_conformance` returns `[]` for both bundled skills.

Traces to DEC-001, DEC-002, DEC-008, DEC-009, DEC-012 of
`plans/super/62-skill-md-layout.md`, extended by DEC-003, DEC-008,
and DEC-014 of `plans/super/71-agentskills-lint.md` (which retired
the in-helper warning tuple; see "Post-#71 update" below).
Companion rules: `.claude/rules/pure-compute-vs-io-split.md` (the
pure-helper shape), `.claude/rules/path-validation.md` (the
regex-and-containment style for user-provided paths from JSON,
though this rule covers Markdown-frontmatter identity rather than
JSON paths).

## Post-#71 update

Before #71, `derive_skill_name` returned `(name, warning | None)`
and `SkillSpec.from_file` / `cmd_init` printed the warning string
(prefix `"clauditor.spec: ..."`) to stderr. #71 introduced
`clauditor.conformance` (a pure module checking SKILL.md against
the agentskills.io spec) and retired the identity helper's warning
channel (DEC-008). The two diagnostics the helper used to produce —
"frontmatter name is not a valid skill identifier" and
"frontmatter name overrides filesystem name" — are now produced by
`check_conformance` as `AGENTSKILLS_NAME_INVALID_CHARS` and
`AGENTSKILLS_NAME_PARENT_DIR_MISMATCH`. Both are emitted at
`severity="error"`. The `SkillSpec.from_file` soft-warn hook filters
to warnings only per DEC-003, so these **errors do not fire at the
hook** — they surface via `clauditor lint` (exit 2), which emits
warnings *and* errors with the
`"clauditor.conformance: <CODE>: <message>"` prefix (DEC-014).
Net effect on this rule: the identity helper is now strictly pure
(`str` return, no side-channel), and frontmatter-name diagnostics
move to an explicit-lint surface rather than a routine-load stderr
line. Routine loads stay silent on name issues (matching the
pre-#71 "lenient on failure" behavior); the author sees them when
they actively run `clauditor lint` before publishing.

## When this rule applies

Any future feature that needs to derive a skill's `skill_name` from a
`SKILL.md` file. Examples:

- A plugin uploader that reads a skill directory and posts it to a
  registry under its frontmatter-declared name.
- A rubric proposer or trigger classifier that needs to reference the
  skill by name in prompts.
- An auto-generated eval spec writer (`clauditor init`, `clauditor
  propose-eval`, a future `clauditor propose-triggers`).
- A skill registry client or local cache keyed by skill name.

The rule also generalizes, shape-wise, to reading other frontmatter
fields (`description:`, `allowed-tools:`, `argument-hint:`) where the
author-provided value is authoritative and a filesystem or spec-local
fallback exists — though each new field needs its own validation
invariant, and any diagnostic-emitting validation for those fields
should go through `conformance.check_conformance` (or a sibling
pure-module checker) rather than back into `derive_skill_name`'s
return channel.

## When this rule does NOT apply

- CLI flags or already-validated config where the user types the
  skill name directly on the command line — the regex validation
  belongs at the CLI-arg layer, not at a frontmatter-reader.
- Non-skill Markdown files with YAML frontmatter (blog posts,
  README teasers, ADRs). The `SKILL.md`-specific fallback assumptions
  (parent dir, stem) don't generalize.
- Direct-constructor test fixtures that bypass `from_file`
  (`SkillSpec(Path("dummy.md"), ...)`). Those use the `__init__`
  no-I/O fallback documented above, not `derive_skill_name`.
- One-off diagnostic scripts in `scripts/` that open a SKILL.md and
  just want `path.stem`. Those should use the helper if they touch
  production identity plumbing, but ad-hoc shell-script-style
  diagnostics are fine with inline logic.
