---
name: investigate
description: Structured investigation — HA logs, API trace, code trace. No code changes until approved.
---

Structured diagnosis protocol. Gather evidence BEFORE proposing any fix. Do NOT write or change any code during investigation.

## Phase 1: Gather Evidence (parallel agents)

Launch three agents in parallel with the problem description:

**Agent 1 — HA Log Analysis**: Read last 100+ lines of verisure_italy logs from HA:
```bash
ssh root@homeassistant -p 22222 "docker logs homeassistant 2>&1 | grep -i verisure_italy | tail -100"
```
Extract ERROR/WARNING with timestamps. Identify event sequence and patterns. Also check for automation-related issues:
```bash
ssh root@homeassistant -p 22222 "docker logs homeassistant 2>&1 | grep -i 'alarm\|arming\|force' | tail -50"
```

**Agent 2 — Entity State**: Check current state of all integration entities via HA API:
```bash
source .env
curl -s "http://homeassistant:8123/api/states" -H "Authorization: Bearer $HA_TOKEN" | \
  python3 -c "import sys,json; [print(f'{s[\"entity_id\"]}: {s[\"state\"]} {s[\"attributes\"]}') for s in json.load(sys.stdin) if 'verisure' in s['entity_id']]"
```
Compare expected vs actual. Check for stale force context, stuck states, unavailable entities.

**Agent 3 — Code Tracing**: Trace the code path from error/symptom. Check recent changes (`git log --oneline -10`). Read the relevant source files. Identify the failure point.

## Phase 2: Synthesize

Correlate findings across all three sources. State root cause with evidence. If unclear, list knowns and unknowns. Propose minimal fix.

**Present diagnosis to the human. Wait for approval before writing any code.**

Do NOT guess. Do NOT "probably because...". Do NOT change code speculatively. Evidence first.
