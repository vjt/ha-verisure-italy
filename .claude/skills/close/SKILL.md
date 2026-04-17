---
name: close
description: End-of-session protocol — push, CHANGELOG, docs, staleness check
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

### 2. Update CHANGELOG.md

If this session shipped user-visible changes (not yet released to PyPI),
add or extend an `## Unreleased` section at the top of `CHANGELOG.md`.

Group entries under the existing taxonomy:
- **Security & Correctness** — fail-secure fixes, state bugs, auth
- **Architecture** — structural refactors, typing improvements
- **UI & UX** — dashboard, entity visibility, notifications
- **API / Protocol** — Verisure API handling changes
- **Tests / Tooling** — test infra, scripts, CI

One bullet per logical change. Lead with the WHAT and a short WHY.
Use the existing CHANGELOG entries as tone reference — concise, direct,
no marketing fluff.

Skip if the session was purely internal (docs, tests, tooling that
doesn't affect users).

### 3. Update living docs (if needed)

Check if this session's work affects any of these. Only update if
content has actually changed:

- `docs/architecture-client.md` — API client internals, proto codes,
  model changes
- `docs/architecture-integration.md` — HA entity model, coordinator,
  state machine, service registrations
- `docs/automations.md` — example automations, HA service contracts
- `docs/hacking.md` — dev workflow, deploy paths, test commands
- `CLAUDE.md` — project memory, engineering standards, protocol ref

Skip docs that weren't affected. Don't touch docs for cosmetic reasons.

**Staleness check:** Grep active docs (`docs/architecture-*.md`,
`docs/automations.md`, `docs/hacking.md`, `CLAUDE.md`) for references
to renamed/removed types, tables, methods, proto codes, or changed
patterns from this session. Fix stale references. Don't touch
`docs/reviews/` or `docs/superpowers/` — historical records.

### 4. Auto-memory update

If the session produced durable knowledge worth carrying into future
sessions (user preferences, surprising API behavior, design tenets,
feedback corrections), update files under
`/home/vjt/.claude/projects/-home-vjt-code-ha-ha-verisure/memory/`.

Skip if nothing new was learned. Do NOT save ephemeral task state,
code patterns (those live in the code), or things already in CLAUDE.md.

### 5. Final commit and push

Commit all doc changes:
```
docs: close session — CHANGELOG + [whatever else changed]
```

Push to origin.

### 6. Report

Tell the human:
- Commits pushed (count + range)
- CHANGELOG updated (which entries added)
- Docs updated (list)
- Memory updated (which files)
- Any pending work for next session
- Release needed? (if unreleased items exist, suggest `verisure-release`)
