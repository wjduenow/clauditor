---
name: release-manager
description: Cut a clauditor-eval release. Test releases run from dev (→ TestPyPI); full releases run from main (→ PyPI).
compatibility: "Requires: uv, gh CLI, git. Must be run from the clauditor repo root."
metadata:
  clauditor-version: "0.0.0-dev"
disable-model-invocation: true
allowed-tools: Bash(git *), Bash(gh *), Bash(uv *), Bash(uvx *), Bash(grep *), Bash(cat *), Bash(sleep *), Bash(pip *), Read, Edit
---

# /release-manager — Cut a clauditor-eval release

You help the maintainer cut a release of `clauditor-eval`.
- **Test releases** run from the `dev` branch and publish to TestPyPI.
- **Full releases** run from the `main` branch and publish to PyPI. The merge from `dev` → `main` is already done before a full release.

## Step 0 — Choose release type

Ask the user:
> **Release type?**
> - `test` — publish to TestPyPI as a pre-release (version must have a dev/alpha/rc suffix)
> - `full` — publish to real PyPI as a stable release (clean version, e.g. `0.1.0`)

Record the choice and follow the matching workflow below.

---

## Pre-flight

Run these checks and STOP if any fail — report the problem clearly and do not proceed.

**Branch check (differs by release type):**
- **Test release**: `git branch --show-current` must return `dev`
- **Full release**: `git branch --show-current` must return `main`

**Checks for both modes:**
1. **Clean working tree**: `git status --porcelain` must be empty
2. **Up to date with origin**:
   - Test: `git fetch origin dev && git status` must show "up to date"
   - Full: `git fetch origin main && git status` must show "up to date"
3. **Tests pass**: `uv run pytest --cov=clauditor --cov-report=term-missing -q`

**Report a pre-flight summary** — always, even when every check passes. Render it as:

```
Pre-flight checks:
- Branch (dev|main): PASS|FAIL
- Clean working tree: PASS|FAIL
- Up to date with origin: PASS|FAIL
- Tests pass: PASS|FAIL
```

Then continue to "Determine version" (on all-PASS) or STOP with the failing check highlighted.

## Determine version

Read `pyproject.toml` and extract the current version.

**For a test release:** version must have a pre-release suffix (`.devN`, `aN`, `bN`, `rcN`).
If the current version is already a pre-release (e.g. `0.1.0.dev3`), use it as-is.
If it is a clean version (e.g. `0.1.0`), stop and tell the user to bump to a dev version first.

**For a full release:** strip the pre-release suffix from the current version.
If the current version is already clean (e.g. `0.1.0`), use it as-is.

Show the user:
```
Current version : {current}
Release version : {release}
Next dev version: {next}   ← only shown for full release; ask user to confirm
```

Ask the user to confirm before proceeding.

---

## Test release workflow

### Step 1 — Build and verify
```bash
rm -rf dist/
uv build
uvx twine check dist/*
```
Both artifacts must show `PASSED`. Stop and report if either fails.

### Step 2 — Commit if version changed
If the version in `pyproject.toml` was not changed (used as-is), skip this step.
Otherwise:
```bash
git add pyproject.toml
git commit -m "chore: bump to {release_version} for test release"
git push origin dev
```

### Step 3 — Tag and create GitHub pre-release
```bash
git tag v{release_version}
git push origin v{release_version}
gh release create v{release_version} \
  --title "v{release_version} (pre-release)" \
  --generate-notes \
  --prerelease \
  --repo wjduenow/clauditor
```
The `--prerelease` flag routes the publish workflow to TestPyPI.

### Step 4 — Monitor publish workflow
```bash
gh run watch --repo wjduenow/clauditor
```
Wait for the `publish-testpypi` job to complete. Report any failures.

### Step 5 — Verify on TestPyPI
```bash
sleep 15
pip index versions clauditor-eval --index-url https://test.pypi.org/simple/ 2>/dev/null | head -3
```
Confirm `{release_version}` appears.

Report: TestPyPI URL `https://test.pypi.org/project/clauditor-eval/{release_version}/`

---

## Full release workflow

### Step 1 — Bump to release version
Edit `pyproject.toml`: set `version = "{release_version}"`.

### Step 2 — Build and verify
```bash
rm -rf dist/
uv build
uvx twine check dist/*
```
Both artifacts must show `PASSED`. Stop and report if either fails.

### Step 3 — Commit, tag, push
```bash
git add pyproject.toml
git commit -m "chore: release {release_version}"
git tag v{release_version}
git push origin main
git push origin v{release_version}
```

### Step 4 — Create GitHub Release
```bash
gh release create v{release_version} \
  --title "v{release_version}" \
  --generate-notes \
  --repo wjduenow/clauditor
```
No `--prerelease` flag — this routes to PyPI.

### Step 5 — Monitor publish workflow
```bash
gh run watch --repo wjduenow/clauditor
```
Wait for the `publish-pypi` job to complete. Report any failures.

### Step 6 — Verify on PyPI
```bash
sleep 30
pip index versions clauditor-eval 2>/dev/null | head -3
```
Confirm `{release_version}` appears.

### Step 7 — Bump to next dev version
Edit `pyproject.toml`: set `version = "{next_dev_version}"`.
```bash
git add pyproject.toml
git commit -m "chore: begin {next_dev_version}"
git push origin main
```

---

## Done

Report a summary including:
- Release type and version
- PyPI or TestPyPI URL
- For full releases: remind the user to merge `main` back into `dev` to pick up the version bump commit
