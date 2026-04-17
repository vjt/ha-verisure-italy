---
name: close
description: End-of-session protocol — push, checkpoint, CHANGELOG, docs, todo
---

Session closing skill. Invoke with `/close` at end of session.

## Steps

### 1. Push unpushed commits

```bash
git log --oneline origin/master..HEAD
```

If commits exist:
```bash
git push
```

### 2. Update CHANGELOG.md (MANDATORY for user-visible changes)

If this session shipped user-visible changes that are not yet
released to PyPI, add or extend an `## Unreleased` section at the
top of `CHANGELOG.md`.

Group entries under the existing taxonomy:
- **Security & Correctness** — fail-secure fixes, state bugs, auth
- **Architecture** — structural refactors, typing improvements
- **UI & UX** — dashboard, entity visibility, notifications
- **API / Protocol** — Verisure API handling changes
- **Tests / Tooling** — test infra, scripts, CI

One bullet per logical change. Lead with the WHAT and a short WHY.
Use existing CHANGELOG entries as tone reference — concise, direct,
no marketing fluff.

Skip only if the session was purely internal (docs-only, test-only,
tooling that doesn't affect users). When skipping, say so in the
final report.

### 3. Flush checkpoint

Find the active checkpoint (`status: active` in `docs/checkpoints/`).
Add a new session section `## Sn: Descriptive Title (YYYY-MM-DD)`.

Content for each session section:
- What was built/fixed, grouped by topic (not chronologically)
- Key technical decisions and why
- Bug fixes with root cause
- Stats line: test count, pyright errors, commit range

Use existing checkpoint sections as format reference. Be concise
but complete — the checkpoint is the permanent record.

### 4. Update todo.md

- **DELETE completed items** — just the line, nothing else. No
  strikethroughs, no "RESOLVED" annotations. Completions go in the
  checkpoint and CHANGELOG, not in todo.
- **Keep all context on pending items** — method names, findings
  pointers, scope details. These are actionable. Never strip context
  from pending work.
- **Fix stale references** — renamed types, methods, proto codes.
- Add new items discovered during work.
- Update wording of in-progress items if scope changed.

### 5. Check if checkpoint needs rotating

Count session headers (`## S`) and total lines in the active
checkpoint.

Rotate if ANY of:
- Active checkpoint has >= 8 sessions
- Active checkpoint exceeds ~200 lines
- The human asks to rotate

**Rotation procedure:**
1. Change `status: active` to `status: done` in frontmatter.
2. Determine next CP number (increment from current).
3. Create new checkpoint file
   `docs/checkpoints/YYYY-MM-DD-cpNN.md` with `status: active`,
   `# CPNN`, and a `Previous:` line summarizing the closed checkpoint.
4. Commit: `docs: close CPxx, create CPyy`.

### 6. Update living docs (if needed)

Check if this session's work affects any of these. Only update if
content has actually changed:

- `docs/architecture-client.md` — API client internals, proto codes,
  models
- `docs/architecture-integration.md` — HA entity model, coordinator,
  state machine, services
- `docs/automations.md` — example automations, HA service contracts
- `docs/hacking.md` — dev workflow, deploy paths, test commands
- `docs/decisions.md` — new architectural decisions or design tenets
- `docs/patterns.md` — new patterns, HA quirks, gotchas
- `docs/findings/*.md` — new reverse-engineered API behavior
- `CLAUDE.md` — project memory, engineering standards, protocol ref

Skip docs that weren't affected. Don't touch docs for cosmetic
reasons.

**Staleness check:** grep active docs (`docs/architecture-*.md`,
`docs/automations.md`, `docs/hacking.md`, `docs/decisions.md`,
`docs/patterns.md`, `CLAUDE.md`) for references to renamed/removed
types, methods, proto codes, or changed patterns from this session.
Fix stale references. Don't touch `docs/reviews/` or
`docs/checkpoints/` — historical records.

### 7. Auto-memory update (user-scoped only)

`~/.claude/projects/-home-vjt-code-ha-ha-verisure/memory/` holds
user/feedback/reference memories only. Update if the session
produced new user preferences, feedback corrections, or external
reference pointers. Project findings belong in `docs/findings/`,
not auto-memory.

Skip if nothing new was learned.

### 8. Final commit and push

Commit all doc changes:
```
docs: close session — checkpoint + CHANGELOG + [whatever else]
```

Push to origin.

### 9. Report

Tell the human:
- Commits pushed (count + range)
- CHANGELOG updated (which entries added, or "skipped — internal
  session")
- Checkpoint: flushed / rotated (and new CP created)
- Docs updated (list)
- Todo: items closed / items added
- Any pending work for next session
- Release needed? (if unreleased items exist, suggest
  `verisure-release`)
