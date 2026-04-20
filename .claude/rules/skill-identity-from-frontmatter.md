# Rule: Derive skill identity from frontmatter first, filesystem second, lenient on failure

When a feature needs to derive a skill's identity (its `skill_name`)
from a `SKILL.md` file, consult the YAML frontmatter `name:` field
first, validate it against `SKILL_NAME_RE`, and fall back to a
**layout-aware** filesystem-derived name when frontmatter is absent or
invalid. The helper is pure — it takes the already-loaded Markdown
text, emits no stderr, and returns a `(name, warning_or_None)` tuple
so the caller can emit warnings at the I/O boundary. Malformed
frontmatter and regex failures are lenient: fall back, warn, keep
going. A typo in YAML should never make a skill uncallable.

## The pattern

```python
# src/clauditor/paths.py — pure, no I/O
SKILL_NAME_RE: str = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"


def _filesystem_name(skill_path: Path) -> str:
    """Layout-aware filesystem fallback."""
    if skill_path.name == "SKILL.md":
        return skill_path.parent.name   # modern: <dir>/SKILL.md
    return skill_path.stem              # legacy: <name>.md


def derive_skill_name(
    skill_path: Path, skill_md_text: str,
) -> tuple[str, str | None]:
    fs_name = _filesystem_name(skill_path)

    from clauditor._frontmatter import parse_frontmatter
    try:
        parsed, _body = parse_frontmatter(skill_md_text)
    except ValueError:
        return fs_name, None  # malformed frontmatter → treat as absent

    if not isinstance(parsed, dict) or "name" not in parsed:
        return fs_name, None  # no name: key → silent fallback

    fm_name = parsed["name"]
    if not isinstance(fm_name, str) or re.fullmatch(SKILL_NAME_RE, fm_name) is None:
        return fs_name, (
            f"clauditor.spec: frontmatter name {fm_name!r} is not a "
            f"valid skill identifier — using {fs_name!r}"
        )

    if fm_name != fs_name:
        return fm_name, (
            f"clauditor.spec: frontmatter name {fm_name!r} overrides "
            f"filesystem name {fs_name!r} — using {fm_name!r}"
        )

    return fm_name, None
```

At the call site, the I/O layer owns `read_text` and stderr:

```python
# src/clauditor/spec.py::SkillSpec.from_file
text = skill_path.read_text(encoding="utf-8")
skill_name, warning = derive_skill_name(skill_path, text)
if warning is not None:
    print(warning, file=sys.stderr)
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
- **Lenient on regex failure, not strict**: a malformed frontmatter
  value shouldn't make the skill uncallable — the filesystem fallback
  is the already-validated path segment (the parent dir / stem went
  through the OS). Fall back, warn, keep going. The warning names
  both the bad value and the fallback so the author notices without
  being blocked.
- **Malformed frontmatter treated as absent**: `parse_frontmatter`
  raises `ValueError` on structural errors (missing closing `---`,
  empty key, etc.). A hard failure would be hostile to authors
  iterating on a skill; silent fallback preserves load-bearing
  behavior. A future `--strict` mode could escalate if needed.
- **Pure `(str, str | None)` tuple return**: the helper emits no
  stderr, touches no disk. Callers emit warnings at the I/O boundary
  (`SkillSpec.from_file`, `cli/init.py`). Satisfies
  `.claude/rules/pure-compute-vs-io-split.md` — tests can assert on
  both tuple elements without `capsys`, and the integration tests that
  use `capsys` are a separate class from the pure-helper tests.
- **Disagreement wins for frontmatter + warns**: when `fm_name !=
  fs_name`, frontmatter wins but the user sees a stderr line. This
  future-proofs against accidental renames (someone moves
  `<dir>/SKILL.md` to `<other-dir>/SKILL.md` but forgets to update the
  frontmatter): the skill still loads under its frontmatter-declared
  identity, and the warning alerts the author to the mismatch.
- **`SKILL_NAME_RE` is a shared constant, not inlined**: two callers
  currently validate against it (`paths.py::derive_skill_name` and
  `propose_eval.py::_derive_skill_name_from_path_or_frontmatter`). A
  third caller — e.g. a future rubric proposer, a plugin uploader, or
  a registry client — should import the constant, not copy the regex.
  A drift between two inlined regexes is a silent security footgun.

## What NOT to do

- Do NOT hard-fail `from_file` when frontmatter is malformed or `name:`
  fails the regex. The fallback path is the minimum-viable identity;
  hard-failing is hostile to authors and masks the real fix site.
- Do NOT emit stderr from inside `derive_skill_name`. The helper is
  pure; stderr belongs to the caller (see
  `.claude/rules/pure-compute-vs-io-split.md`).
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

## Canonical implementation

- Shared regex: `src/clauditor/paths.py::SKILL_NAME_RE`.
- Pure helpers: `src/clauditor/paths.py::_filesystem_name` +
  `derive_skill_name`.
- I/O caller: `src/clauditor/spec.py::SkillSpec.from_file` — reads the
  file, calls the helper, emits any warning to stderr, passes the
  resolved name to `SkillSpec.__init__` via the keyword-only
  `skill_name_override=` kwarg.
- Back-compat shape: `SkillSpec.__init__` accepts
  `skill_name_override: str | None = None` and falls back to a
  layout-aware no-I/O derivation when the override is `None` (preserves
  the direct-constructor path used by `tests/test_quality_grader.py`'s
  `SkillSpec(Path("dummy.md"), ...)` fixture).
- Second caller: `src/clauditor/cli/init.py::cmd_init` — reads the
  file, calls the same helper, emits the warning, uses the name in the
  starter eval's `skill_name` and `description` fields.
- Tests: `tests/test_paths.py::TestDeriveSkillName` (seven pure-helper
  cases, no `tmp_path`) and `tests/test_spec.py::TestFromFile` (five
  integration cases covering both layouts + `capsys` for warning
  emission).
- Regression test: `tests/test_bundled_skill.py::TestBundledSkillViaSpec`
  loads the project's own modern-layout bundled `SKILL.md` through
  `SkillSpec.from_file` — a real-file self-validation.

Traces to DEC-001, DEC-002, DEC-008, DEC-009, DEC-012 of
`plans/super/62-skill-md-layout.md`. Companion rules:
`.claude/rules/pure-compute-vs-io-split.md` (the pure-helper shape),
`.claude/rules/path-validation.md` (the regex-and-containment style
for user-provided paths from JSON, though this rule covers
Markdown-frontmatter identity rather than JSON paths).

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
invariant.

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
