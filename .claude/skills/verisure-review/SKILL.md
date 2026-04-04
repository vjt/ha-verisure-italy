---
name: verisure-review
description: Dispatch 8 parallel agents for full codebase review (line-level + architecture)
---

Run a full codebase review. No argument needed — this project is small
enough that both line-level and architecture reviews run together in
one pass.

Full protocol below.

## Dispatch

4 parallel background agents for line-level review + 4 for architecture:

### Line-level agents

| Agent | Scope |
|-------|-------|
| client | `verisure_italy/` — client.py, models.py, exceptions.py, graphql.py, responses.py |
| integration-core | `custom_components/verisure_italy/` — coordinator.py, alarm_control_panel.py, config_flow.py, __init__.py, const.py |
| integration-ui | `custom_components/verisure_italy/` — button.py, camera.py, dashboard.py |
| tests+cross | `tests/` + cross-module patterns across ALL source files |

Each agent MUST read EVERY file in scope + `CLAUDE.md` + `docs/hacking.md`.

### Agent instructions (include in every line-level agent prompt)

Report PROBLEMS ONLY. No praise. For each finding:

```
### S{N}. Short title
**File:** `path:line`
**Category:** category tag
**Severity:** CRITICAL/HIGH/MEDIUM/LOW
Description.
**Fix:** Concrete suggestion.
```

What to look for:
- Dead code (unused functions, imports, variables, unreachable branches)
- Default arguments (`= None`, `= []`, `= {}`) — only `timeout=30`-style config defaults acceptable
- Untyped / weakly-typed (`dict[str, Any]`, bare `dict`, `str` where enum exists, `Any`, missing return types)
- Abstraction leaks (returning raw dicts/strings callers must parse)
- Swallowed exceptions (bare `except:`, `except Exception: pass`, log-and-continue)
- Security issues (alarm state handling, credential exposure, fail-open paths, force context races)
- State machine bugs (force context lifecycle, coordinator update suppression gaps)
- Missing error handling for client exceptions in integration code
- Stale patterns contradicting CLAUDE.md
- Unused entity attributes, stale coordinator data references

What to IGNORE: style preferences, "could be improved" but not bugs, HA framework boilerplate.

### Cross-module agent (tests+cross) additions

Additionally search the ENTIRE codebase for:
- `= None` in all function/method signatures
- `dict[str, Any]` and bare dict usage
- `Any` type usage
- `except Exception` patterns (log-and-continue vs re-raise)
- Client exceptions not caught in integration code
- Inconsistencies between client API and integration usage
- Dead imports across all files

### Architecture agents

| Agent | Concern |
|-------|---------|
| Boundaries | Client ↔ integration boundary. Exceptions fully covered? Pydantic models leaking into HA entities? Integration depending on client internals? |
| State management | Force context lifecycle, `_arm_in_progress` flag, coordinator update suppression, entity availability, race conditions between polling and user actions |
| HA patterns | HA conventions followed? CoordinatorEntity usage, entity naming, config flow, service registration, dashboard internals usage risk |
| Security model | Fail-secure behavior end-to-end. Unknown states crash. Timeouts preserve state. Credentials not logged. Admin vs restricted. Force-arm suid. |

### Architecture agent instructions

Report FINDINGS, not line-level bugs. For each:

```
### A{N}. Short title
**Concern:** which of the 4
**Scope:** modules / files involved
**Problem:** structural issue
**Impact:** what breaks, drifts, or gets harder
**Recommendation:** concrete path forward
```

Severity: CRITICAL (blocks correctness/safety), HIGH (maintenance burden), MEDIUM (tech debt), LOW (improvement).

## After all agents complete

1. Collect all findings from all 8 agents.
2. Deduplicate (cross-module agent may overlap with scope agents).
3. **Trajectory review**: answer: what did we build, does it serve the
   mission (replace Verisure app with HA), what's missing, risk check,
   and a 2-3 sentence direction recommendation.
4. Compile into: `docs/reviews/YYYY-MM-DD-codebase-review.md`
5. Summary table: severity counts by agent scope.
6. Present top findings + trajectory to user.
