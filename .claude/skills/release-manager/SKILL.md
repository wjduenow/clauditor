---
name: release-manager
description: Cut a clauditor-eval release from main. Handles version bump, build, tag, GitHub Release creation, PyPI publish verification, and post-release dev bump. Run this after merging dev → main.
compatibility: "Requires: uv, gh CLI, git. Must be run from the clauditor repo root on the main branch."
metadata:
  clauditor-version: "0.0.0-dev"
disable-model-invocation: true
allowed-tools: Bash(git *), Bash(gh *), Bash(uv *), Bash(uvx *), Bash(grep *), Bash(cat *), Read, Edit
---

# /release-manager — Cut a clauditor-eval release

You help the maintainer cut a release of `clauditor-eval` from the `main`
branch. The merge from `dev` → `main` is already done before this skill runs.

## Pre-flight

Run these checks and STOP if any fail — report the problem clearly and do not proceed:

1. **On main**: `git branch --show-current` must return `main`
2. **Clean working tree**: `git status --porcelain` must be empty
3. **Up to date with origin**: `git fetch origin main && git status` must show "up to date"
4. **Tests pass**: `uv run pytest --cov=clauditor --cov-report=term-missing -q`

## Determine release version

Read `pyproject.toml` and extract the current version (e.g. `0.1.0.dev3`).
Strip the `.devN` suffix to get the release version (e.g. `0.1.0`).

Show the user:
```
Current version : 0.1.0.dev3
Release version : 0.1.0
Next dev version: 0.2.0.dev1   ← ask user to confirm this or suggest alternative
```

Ask the user to confirm before proceeding.

## Release steps

Run these in order. After each step confirm success before moving to the next.

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
This triggers `publish.yml` → PyPI publish automatically.

### Step 5 — Monitor publish workflow
```bash
gh run watch --repo wjduenow/clauditor
```
Wait for the `publish.yml` run to complete. If it fails, report the error and stop — do not proceed to the version bump.

### Step 6 — Verify on PyPI
```bash
sleep 30  # allow PyPI index to update
pip index versions clauditor-eval 2>/dev/null | head -3
```
Confirm `{release_version}` appears in the output.

### Step 7 — Bump to next dev version
Edit `pyproject.toml`: set `version = "{next_dev_version}"`.
```bash
git add pyproject.toml
git commit -m "chore: begin {next_dev_version}"
git push origin main
```

## Done

Report a summary:
- Released version and PyPI URL: `https://pypi.org/project/clauditor-eval/{release_version}/`
- Next dev version now on `main`
- Remind the user to merge `main` back into `dev` to pick up the version bump
