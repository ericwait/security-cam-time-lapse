---
description: Upgrade the lockfile, rebuild, run tests — stops on regression
---

You are running the `/deps-update` skill. WRITES to the lockfiles and rebuilds
the environment. **Never commits** — it leaves a reviewable diff for the
human to inspect and commit themselves.

## 1. Require a clean tree

`git status --porcelain` must be empty. Abort otherwise: "Uncommitted
changes — commit or stash before running `/deps-update`."

## 2. Upgrade the lockfiles

```bash
docker compose run --rm app uv lock --upgrade
docker compose run --rm web sh -c "npm update && npm install"
```

Report the set of packages that moved (name, old version to new version), and
verify whether `.pre-commit-config.yaml` hook pins (`ruff`, etc.) need to bump
in lockstep with the lockfile per the PIN RULE.

## 3. Rebuild / reinstall

Rebuild the container images from the updated lockfiles:

```bash
docker compose build
```

**On build failure:**

```bash
git checkout -- backend/uv.lock frontend/package-lock.json frontend/package.json
```

Report the failing package(s) and stop. Roll all the way back.

## 4. Run tests

```bash
just test
```

**On test failure:** do **not** roll back. Leave the lockfile diff in place so
the human can inspect what changed. Print which tests failed, then stop.

## On success

Report:

- Number of packages upgraded, with any load-bearing ones called out by name.
- Test result summary.
- A reminder that the lockfiles are modified and uncommitted — review the diff
  (including `.pre-commit-config.yaml` hook sync), then commit yourself.
