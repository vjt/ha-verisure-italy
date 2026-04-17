---
name: start
description: Session start protocol — workflow gates, pending work, status report
---

Session start skill. Run the full workflow checklist and produce a
status report. This is the "what's pending" dashboard.

## Steps

### 1. Codebase review gate (the ONLY gate)

Check date of the most recent review in `docs/reviews/` (filenames are
dated `YYYY-MM-DD-*.md`). Count commits since that date with
`git log --oneline --since=<date> | wc -l`.

A review is **DUE** if:
- >= 50 commits since last review, OR
- > 4 weeks since last review

**When due: must run before new feature work.** Bug fixes and deploy
fixes are exempt. Use the `verisure-review` skill to run it.
This is enforced, not advisory.

### 2. Check CHANGELOG unreleased

Read the top of `CHANGELOG.md`. If there is an `## Unreleased` section
or the latest version entry is newer than the installed HACS version
(check `hacs.json` + latest git tag), flag it — these are items shipped
to HAOS but not yet released to users via PyPI + GitHub.

### 3. Active plan / spec

Check `docs/superpowers/plans/` for the newest plan file. If dated
within the last 2 weeks, report it as active — its unfinished steps
are pending work.

### 4. Check git status

```bash
git status
git log --oneline origin/master..HEAD
git log --oneline -5
```

Note uncommitted changes, unpushed commits, stash entries, worktrees.

### 5. Produce the report

Format as follows:

```
🔬 **Codebase Review**: not due (N commits since YYYY-MM-DD) / DUE — run `verisure-review` before features
📦 **Release State**: clean / UNRELEASED: vX.Y.Z in CHANGELOG, not yet on PyPI
🌿 **Git State**: clean / N uncommitted / N unpushed / worktree at path
📋 **Active Plan**: none / docs/superpowers/plans/YYYY-MM-DD-*.md (N steps left)

## What's Available
Given the gate status, here's what we can work on: ...
```

The "What's Available" section is the key output — if a codebase
review is due, say so and offer to run it. If there are unreleased
items, offer to release. Otherwise, list priority work from the
active plan or recent commits.
