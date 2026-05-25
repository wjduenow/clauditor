# clauditor-eval

Node.js wrapper for [clauditor](https://github.com/wjduenow/clauditor) — an
auditor for Claude Code skills and slash commands.

> **Status: skeleton (US-002).** The public JS API (`runSkill`, `validate`,
> `loadSpec`), the Jest/Vitest matcher, and the CLI launcher land in later
> stories. Full documentation arrives in US-007.

## What this is

`clauditor-eval` is a **subprocess bridge**: it shells out to the Python
`clauditor` engine (`pip install clauditor-eval`) rather than
reimplementing the evaluation layers in JavaScript. v1 ships **no
PyInstaller binary and no platform packages** — the Python engine must be
installed separately.

## Requirements

- Node.js `>=18`
- The Python `clauditor` engine on `PATH` (or reachable via
  `python -m clauditor`).

## Naming and versioning

- **Package name `clauditor-eval`.** The unscoped npm name `clauditor` is
  already taken by an unrelated owner, so this package uses
  `clauditor-eval`, matching the PyPI package name (DEC-001).
- **npm version `0.1.0`.** The Python engine's version
  (`pyproject.toml [project].version`, currently `0.1.3.dev0`) is not a
  valid npm semver. Rather than mechanically transliterate it, the initial
  npm release uses the clean semver `0.1.0`. Future npm releases track the
  npm package's own changes; they are not pinned 1:1 to the PyPI version.

## Reserved scope

The npm scope `@clauditor-eval/*` is **RESERVED** for a future
PyInstaller binary-distribution epic (DEC-013). It is intentionally unused
in v1 — there are no `optionalDependencies` and no per-platform packages.
The scope is documented here so the binary follow-up can claim it without
renaming this package.

## License

Apache-2.0. See [LICENSE](./LICENSE).
