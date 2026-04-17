---
name: start
description: Session start protocol — workflow gates, pending work, status report
---

Session start skill. Run the full workflow checklist and produce a
status report. This is the "what's pending" dashboard.

## Steps

### 1. Codebase review gate (the ONLY gate)

Check the most recent review in `docs/reviews/` (filenames are dated
`YYYY-MM-DD-*.md`). Count commits since that date with
`git log --oneline --since=<date> | wc -l`.

A review is **DUE** if:
- >= 50 commits since last review, OR
- > 4 weeks since last review

**When due: must run before new feature work.** Bug fixes and deploy
fixes are exempt. Use the `verisure-review` skill to run it. This is
enforced, not advisory.

### 2. Find active checkpoint

Find the checkpoint with `status: active` in `docs/checkpoints/`.
Report:
- CP number and how many sessions it has (count `## S` headers)
- Line count — warn if approaching 200 (time to rotate)

### 3. Read todo + changelog

- `docs/todo.md` — full backlog, categorize by tier
  (Immediate / High / Medium / Observation)
- `CHANGELOG.md` top — if an `## Unreleased` section exists or the
  latest version entry is newer than the installed HACS tag, flag
  it: these are items shipped to HAOS but not yet released to users
  via PyPI + GitHub.

### 4. Check git status

```bash
git status
git log --oneline origin/master..HEAD
git log --oneline -5
```

Note uncommitted changes, unpushed commits, stash entries, worktrees.

### 5. Produce the report

Format the report as follows:

```
🔬 **Codebase Review**: not due (N commits since YYYY-MM-DD) / DUE — run `verisure-review` before features
📍 **Active Checkpoint**: CPnn (n sessions, ~nnn lines)
📦 **Release State**: clean / UNRELEASED: vX.Y.Z in CHANGELOG, not yet on PyPI
🌿 **Git State**: clean / N uncommitted / N unpushed / worktree at path

## Todo Highlights
**Immediate**: ...
**High**: ...
**Medium**: ...
**Observation**: ...

## What's Available
Given the gate status, here's what we can work on: ...
```

The "What's Available" section is the key output — if a review is due,
say so and offer to run it. If unreleased items exist, offer to
release. Otherwise, list priority work from todo + active checkpoint.
