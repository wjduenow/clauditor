---
name: release-manager
description: Cut a clauditor-eval release. Test releases run from dev (→ TestPyPI, npm dist-tag `next`); full releases run from main (→ PyPI, npm `latest`). Python and npm version independently — release either or both.
compatibility: "Requires: uv, gh CLI, git, node/npm. Must be run from the clauditor repo root."
metadata:
  clauditor-version: "0.1.0"
disable-model-invocation: true
allowed-tools: Bash(git *), Bash(gh *), Bash(uv *), Bash(uvx *), Bash(npm *), Bash(node *), Bash(grep *), Bash(cat *), Bash(sleep *), Bash(pip *), Bash(curl *), Bash(awk *), Bash(rm *), Bash(date *), Read, Edit, Write
---

# /release-manager — Cut a clauditor-eval release

You help the maintainer cut a release of `clauditor-eval`. There are **two
independently-versioned artifacts**:

- **Python engine** → PyPI (test: TestPyPI). Version lives in `pyproject.toml`.
- **npm wrapper** → npm registry. Version lives in `npm/package.json`. The npm
  package versions **independently** of the Python engine (they are not kept in
  lockstep — `npm/package.json` may be `0.1.0` while `pyproject.toml` is
  `0.1.4.dev0`), so it is released only when the wrapper itself changed.

Branch model is shared:
- **Test releases** run from the `dev` branch (→ TestPyPI, and/or npm dist-tag `next`).
- **Full releases** run from the `main` branch (→ PyPI, and/or npm `latest`). The merge from `dev` → `main` is already done before a full release.

## Step 0 — Choose release type and components

Ask the user **two** questions:

> **1. Release type?**
> - `test` — pre-release (Python version must have a dev/alpha/rc suffix; npm version must be a semver prerelease like `0.1.1-rc.0`)
> - `full` — stable release (clean versions, e.g. `0.1.0`)

> **2. Which components?**
> - `pypi` — Python engine only
> - `npm` — npm wrapper only
> - `both` — release both this cycle

Record both choices. The release **type** selects the branch (test → `dev`,
full → `main`); the **components** select which of the PyPI / npm tracks below
to run. Skip any track the user did not select. When `both` is chosen, run the
PyPI track first, then the npm track within the same workflow.

---

## Pre-flight

Run these checks and STOP if any fail — report the problem clearly and do not proceed.

**Branch check (differs by release type):**
- **Test release**: `git branch --show-current` must return `dev`
- **Full release**: `git branch --show-current` must return `main`

**Checks for both modes:**
1. **Clean working tree**: `git status --porcelain` must be empty.
   - If the output contains modified/staged entries (`M`, `A`, `D`, `R`), STOP and report — these are real changes the user must resolve.
   - If the output contains ONLY untracked entries (`??` prefix), do NOT auto-stop. List them and ask the user how to proceed: stash (`git stash push -u`), add to `.gitignore`, commit, or inspect first. After the release completes, pop the stash if one was created.
2. **Up to date with origin**:
   - Test: `git fetch origin dev && git status` must show "up to date"
   - Full: `git fetch origin main && git status` must show "up to date"
3. **Tests pass**: `uv run pytest --cov=clauditor --cov-report=term-missing -q`
4. **CHANGELOG `[Unreleased]` is current**. Read `CHANGELOG.md` and show the user the current `[Unreleased]` section. Ask: "Does this cover everything shipping in this release?" Pause for confirmation before continuing — `[Unreleased]` becomes the GitHub Release body via `--notes-file` (full release Step 5), so empty / stale content there means an empty / stale release page. If the user wants to update it, stop here, let them edit, then re-run pre-flight.

**Additional checks when the `npm` component is selected** (skip entirely for `pypi`-only):

5. **npm package tests pass**: `cd npm && npm install && npm test && npm run test:vitest && npm run lint` — the publish workflow runs these too, but failing fast here avoids a tagged-but-broken release.
6. **npm version not already published** (the failure that bit us before). Read `npm/package.json` and probe the registry — npm **permanently** rejects re-publishing an existing version, so catch it *before* tagging:

   ```bash
   nver=$(node -p "require('./npm/package.json').version")
   if npm view "clauditor-eval@${nver}" version >/dev/null 2>&1; then
     echo "npm clauditor-eval@${nver} already published — bump npm/package.json"
   fi
   ```

**Report a pre-flight summary** — always, even when every check passes. Render it as (include the npm rows only when the `npm` component is selected):

```
Pre-flight checks:
- Branch (dev|main): PASS|FAIL
- Clean working tree: PASS|FAIL
- Up to date with origin: PASS|FAIL
- Tests pass: PASS|FAIL
- CHANGELOG [Unreleased] reviewed: PASS|FAIL
- npm package tests (npm only): PASS|FAIL
- npm version not yet published (npm only): PASS|FAIL
```

Then continue to "Determine version" (on all-PASS) or STOP with the failing check highlighted.

## Determine version

> Run the **PyPI version** block below only when the `pypi` or `both` component
> is selected; run the **npm version** block only when `npm` or `both`. The two
> versions are independent — do not derive one from the other.

### PyPI version

Read `pyproject.toml` and extract the current version.

**For a test release:** version must have a pre-release suffix (`.devN`, `aN`, `bN`, `rcN`).
If the current version is already a pre-release (e.g. `0.1.0.dev3`), use it as the candidate.
If it is a clean version (e.g. `0.1.0`), stop and tell the user to bump to a dev version first.

Then check TestPyPI to make sure the candidate has not already been published — TestPyPI rejects re-uploads of the same version, and discovering this after the GitHub release tag is pushed is painful:

```bash
candidate=$(grep '^version' pyproject.toml | cut -d'"' -f2)
if curl -sf "https://test.pypi.org/pypi/clauditor-eval/${candidate}/json" >/dev/null; then
  echo "Version ${candidate} already on TestPyPI — bump required"
fi
```

If the candidate already exists on TestPyPI, propose the next `.devN` bump (e.g. `0.1.0.dev5` → `0.1.0.dev6`) and ask the user to confirm. On confirmation, edit `pyproject.toml` to set `version = "{next_dev_version}"` and re-run the TestPyPI check against the new candidate (in case that one was also published). If the candidate does **not** exist on TestPyPI, use it as-is — no edit needed.

**For a full release:** strip the pre-release suffix from the current version.
If the current version is already clean (e.g. `0.1.0`), use it as-is.

Show the user:
```
Current version : {current}
Release version : {release}
Next dev version: {next}   ← only shown for full release; ask user to confirm
```

Ask the user to confirm before proceeding.

### npm version

Read `npm/package.json` and extract the current version
(`node -p "require('./npm/package.json').version"`). npm uses **semver
prerelease** syntax (hyphen), NOT Python's `.devN` — `0.1.1-rc.0`,
`0.1.1-beta.2`, etc.

**For a test release:** the version must be a semver prerelease (contains a
`-`). The publish workflow auto-detects the `-` and publishes under the `next`
dist-tag (testers run `npm install clauditor-eval@next`; plain installs keep
resolving `latest`). If `npm/package.json` is a clean version, stop and ask the
user to bump it to a `-rc.N` / `-beta.N` first.

**For a full release:** the version must be clean (no `-`). It publishes to the
`latest` dist-tag.

In both cases the pre-flight registry probe already confirmed the version is
unpublished. Show the user:
```
npm current version : {npm_current}
npm release version : {npm_release}   (dist-tag: next | latest)
```

Ask the user to confirm before proceeding.

---

## Test release workflow

> Run the **PyPI track** (Steps 1–5) when `pypi`/`both` is selected, then the
> **npm track** when `npm`/`both` is selected. Skip a track that was not chosen.

### PyPI track (TestPyPI)

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

### npm track (dist-tag `next`)

Run only when `npm`/`both` is selected. The npm version must be a semver
prerelease (`-rc.N` / `-beta.N`); the publish workflow auto-detects the `-` and
publishes under the `next` dist-tag via OIDC trusted publishing (no token).

#### Step N1 — Commit if `npm/package.json` changed
If the npm version was edited during "Determine version", commit it to `dev`:
```bash
git add npm/package.json
git commit -m "chore(npm): bump to {npm_release_version} for test release"
git push origin dev
```
If it was used as-is, skip.

#### Step N2 — Tag and trigger the npm publish workflow
```bash
git tag npm-v{npm_release_version}
git push origin npm-v{npm_release_version}
```
This fires `.github/workflows/npm-publish.yml`. There is **no GitHub Release**
for npm tags — the tag push alone is the trigger (unlike PyPI, which publishes
on a GitHub Release).

#### Step N3 — Monitor
```bash
gh run watch --repo wjduenow/clauditor
```
Wait for the `publish-npm` job. Report any failures. (A 403 "you may not perform
that action" means the Trusted Publisher / OIDC config regressed; a 403 "cannot
publish over previously published versions" means the version wasn't bumped.)

#### Step N4 — Verify
```bash
sleep 10
npm view clauditor-eval@next version
```
Confirm `{npm_release_version}` appears under the `next` tag, and that
`npm view clauditor-eval version` (the `latest` tag) is unchanged.

Report: `npm install clauditor-eval@next` → `{npm_release_version}`.

---

## Full release workflow

> **Note:** `main` has GitHub branch protection — direct pushes are rejected. The release-version commit and the next-dev bump both ship via PR.
>
> When `npm`/`both` is selected, the `npm/package.json` bump rides the **same
> release PR** (Step 3) so main carries both version bumps atomically; the
> `npm-v` tag is then pushed alongside the `v` tag after the merge (npm track
> below, after Step 7). When `pypi`-only, skip every npm-flavored note here.

### Step 1 — Bump to release version
Edit `pyproject.toml`: set `version = "{release_version}"`. Then refresh the lock file so `uv.lock` matches:
```bash
uv sync
```

**If `npm`/`both`:** also edit `npm/package.json` to set the clean
`{npm_release_version}` (no `-` suffix) determined earlier.

### Step 1b — Promote CHANGELOG `[Unreleased]` to `[{release_version}]`
Edit `CHANGELOG.md` so this release has its own dated section that the GitHub Release body can quote verbatim:

1. Insert a new dated header **directly after** the existing `## [Unreleased]` line:
   ```
   ## [Unreleased]

   ## [{release_version}] - {today_iso}
   ```
   where `{today_iso}` = `date +%Y-%m-%d`. Do **not** move the existing entries — leaving them under `[{release_version}]` is exactly the desired result; the new empty `[Unreleased]` above is what future entries land in.
2. Update the bottom reference link table:
   - Change the `[Unreleased]` link to `compare/v{release_version}...HEAD`.
   - Add `[{release_version}]: https://github.com/wjduenow/clauditor/releases/tag/v{release_version}` directly below it.

### Step 2 — Build and verify
```bash
rm -rf dist/
uv build
uvx twine check dist/*
```
Both artifacts must show `PASSED`. Stop and report if either fails.

### Step 3 — Open release PR
Push the version bump and the CHANGELOG promotion together on a release branch, then open a PR — direct push to `main` is blocked by branch protection.
When `npm`/`both`, add `npm/package.json` to the same commit so both bumps land atomically on main.
```bash
git checkout -b release/{release_version}
git add pyproject.toml uv.lock CHANGELOG.md
# If npm/both: also stage the npm bump
git add npm/package.json            # npm/both only
git commit -m "chore: release {release_version}"
git push -u origin release/{release_version}
gh pr create --base main --head release/{release_version} \
  --title "chore: release {release_version}" \
  --body "Cuts v{release_version} to PyPI. Pre-flight tests pass; \`uv build\` + \`uvx twine check\` PASSED on both wheel and sdist. CHANGELOG promoted from \`[Unreleased]\`."
```
Stop and ask the user to merge the PR via GitHub. Once merged, continue.

### Step 4 — Pull main, tag, push tag
```bash
git checkout main
git pull origin main
git tag v{release_version}      # tags origin/main HEAD (the merge commit)
git push origin v{release_version}
```

### Step 5 — Create GitHub Release
Extract the just-promoted CHANGELOG section into a temp file and use it as the release body:
```bash
awk -v ver="{release_version}" '
  $0 ~ "^## \\["ver"\\] -" { capturing=1; next }
  capturing && /^## \[/ { exit }
  capturing { print }
' CHANGELOG.md > .release-notes.md

gh release create v{release_version} \
  --title "v{release_version}" \
  --notes-file .release-notes.md \
  --repo wjduenow/clauditor

rm .release-notes.md
```
No `--prerelease` flag — this routes to PyPI. `--notes-file` (not `--generate-notes`) uses the curated CHANGELOG section verbatim instead of an auto-generated PR list.

### Step 6 — Monitor publish workflow
```bash
gh run watch --repo wjduenow/clauditor
```
Wait for the `publish-pypi` job to complete. Report any failures.

### Step 7 — Verify on PyPI
```bash
curl -sf "https://pypi.org/pypi/clauditor-eval/{release_version}/json" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('version:', d['info']['version'])"
```
Confirm `{release_version}` appears.

### npm track (dist-tag `latest`)

Run only when `npm`/`both` is selected. The `npm/package.json` bump already
landed on `main` via the release PR (Step 3) and was pulled in Step 4, so the
tag push below publishes the clean version to `latest` via OIDC (no token, no
GitHub Release — the `npm-v` tag push alone triggers the workflow).

#### Step 7-npm-a — Tag and trigger
```bash
git checkout main && git pull origin main      # ensure the npm bump is present
git tag npm-v{npm_release_version}
git push origin npm-v{npm_release_version}
```

#### Step 7-npm-b — Monitor and verify
```bash
gh run watch --repo wjduenow/clauditor
sleep 10
npm view clauditor-eval version
```
Wait for the `publish-npm` job. Confirm `npm view clauditor-eval version`
(the `latest` tag) now reports `{npm_release_version}`.

Report: `npm install clauditor-eval` → `{npm_release_version}`.

> npm has no next-dev bump or backmerge equivalent (Steps 8–9 are PyPI-only).
> The next npm release simply edits `npm/package.json` again when the wrapper
> next changes.

### Step 8 — Open next-dev bump PR
Edit `pyproject.toml`: set `version = "{next_dev_version}"`. Refresh the lock, then push via PR:
```bash
uv sync
git checkout -b chore/begin-{next_dev_version}
git add pyproject.toml uv.lock
git commit -m "chore: begin {next_dev_version}"
git push -u origin chore/begin-{next_dev_version}
gh pr create --base main --head chore/begin-{next_dev_version} \
  --title "chore: begin {next_dev_version}" \
  --body "Bumps version to {next_dev_version} after the v{release_version} release."
```
Ask the user to merge.

### Step 9 — Backmerge main → dev
After both PRs are merged, sync `dev` with the new `main` so the next test release starts from the bumped version. Open a backmerge PR:
```bash
git checkout dev
git pull origin dev
git checkout -b chore/sync-main-into-dev
git merge origin/main
git push -u origin chore/sync-main-into-dev
gh pr create --base dev --head chore/sync-main-into-dev \
  --title "chore: sync main into dev after {release_version} release" \
  --body "Brings the release commits and the {next_dev_version} bump back to dev."
```
If `dev` allows direct push and the merge fast-forwards cleanly, you may instead `git push origin dev` after the merge — check repo rules first.

---

## Done

Report a summary including:
- Release type and components released (PyPI / npm / both)
- PyPI or TestPyPI URL (when the PyPI track ran)
- npm install hint (when the npm track ran): `clauditor-eval@{npm_version}` for a
  test release (`next` tag) or `clauditor-eval` for a full release (`latest` tag)
- For full releases: confirm the next-dev bump PR (Step 8) and the backmerge PR (Step 9) are open or merged
- If a stash was created during pre-flight, run `git stash pop` and confirm the file came back cleanly
