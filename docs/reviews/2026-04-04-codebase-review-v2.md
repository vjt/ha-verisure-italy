# Codebase Review v2 — 2026-04-04

Full review of ha-verisure-italy at commit `012c6d6` (post-v0.7.0 fixes).
8 parallel agents: 4 line-level (client, integration-core, integration-ui,
tests+cross) and 4 architecture (boundaries, state management, HA patterns,
security model).

## Summary Table

| Severity | Count | Key findings |
|----------|-------|-------------|
| CRITICAL | 0 | — |
| HIGH | 4 | SessionExpiredError uncaught in arm/disarm, ARMING state lie during force-arm, no duplicate installation guard, arm error details discarded |
| MEDIUM | 21 | (see below) |
| LOW | 18 | (see below) |

### Severity by agent scope

| Agent | CRITICAL | HIGH | MEDIUM | LOW |
|-------|----------|------|--------|-----|
| Client (line) | 0 | 1 | 4 | 5 |
| Integration Core (line) | 0 | 0 | 3 | 8 |
| Integration UI (line) | 0 | 0 | 2 | 4 |
| Tests + Cross (line) | 0 | 0 | 4 | 3 |
| Boundaries (arch) | 0 | 1 | 2 | 2 |
| State Management (arch) | 0 | 0 | 3 | 5 |
| HA Patterns (arch) | 0 | 1 | 5 | 3 |
| Security Model (arch) | 0 | 1 | 3 | 3 |

Note: after deduplication, raw count (57 findings) reduces to 43 unique.

---

## HIGH Findings

### H1. SessionExpiredError not caught in arm/disarm entity paths
**Source:** Boundaries A2, Security (implied)
**Scope:** `alarm_control_panel.py:158-233`
**Problem:** The coordinator's `_async_update_data` handles `SessionExpiredError`
with re-login + retry. The alarm entity's `_async_arm`, `async_alarm_disarm`,
and `async_force_arm` do NOT catch `SessionExpiredError`. They catch
`OperationFailedError`, `OperationTimeoutError`, and generic `VerisureError`.

The client's `_ensure_auth()` handles token refresh proactively, but
`SessionExpiredError` can also be raised by `_check_graphql_errors()` during
arm status polling (mid-operation, after `_ensure_auth` passed). If the auth
token expires mid-arm-poll, `SessionExpiredError` surfaces through the
`VerisureError` catch-all as "Arm failed (unexpected)" with no recovery.

**Impact:** Arm operation appears to fail in HA while the physical panel may
have completed arming. User sees ARMING + error notification but alarm is
actually armed. Security-relevant state desync.

**Fix:** Add `SessionExpiredError` to the explicit catch list in all three
arm/disarm methods. On catch: re-login via `coordinator.client.login()`,
then trigger `coordinator.async_request_refresh()` to reconcile state.

---

### H2. Alarm entity shows ARMING while panel is actually DISARMED during force-arm window
**Source:** Security A1
**Scope:** `alarm_control_panel.py:158-183, 133-146`
**Previously flagged:** post-v0.7.0 review M3 — still unfixed

**Problem:** When `_async_arm` catches `ArmingExceptionError`, the entity state
is `ARMING` (set at line 166). The method sets force context and returns
without reverting state. `_handle_coordinator_update` skips state updates while
force context exists. The physical panel is DISARMED but HA reports ARMING.

**Impact:** Automations checking `state == 'disarmed'` (e.g., "if alarm
disarmed when nobody home, send alert") will NOT fire. The user believes the
alarm is in transition when it is fully disarmed. Up to 120 seconds of
inaccurate state on security software.

**Fix:** After catching `ArmingExceptionError`, set state back to the real
panel state before setting force context. Use `extra_state_attributes` to
communicate the pending force-arm option. Reconsider the poll suppression
guard accordingly.

---

### H3. No duplicate installation guard in config flow
**Source:** HA Patterns A2
**Scope:** `config_flow.py`

**Problem:** The config flow never calls `async_set_unique_id(installation.number)`
+ `_abort_if_unique_id_configured()`. A user can add the same installation
twice, creating duplicate entities and two coordinators polling the same panel.

**Impact:** Duplicate entries → double API traffic (WAF risk), duplicate
entities, services affecting only the first coordinator. For security software,
duplicate arm commands create race conditions.

**Fix:** In `async_step_installation`, add:
```python
await self.async_set_unique_id(installation.number)
self._abort_if_unique_id_configured()
```

---

### H4. Arm error details discarded by generic poll machinery
**Source:** Client S1
**Scope:** `client.py:786-794, 1233-1238`

**Problem:** When `_check_arm_status_once` encounters `res == "ERROR"` but it's
not a NON_BLOCKING force-arm-eligible error, it wraps the result as a plain
`OperationResult`. Then `_poll_operation` raises `OperationFailedError` with
`error_code=None, error_type=None` — discarding `arm_result.error.code` and
`arm_result.error.type`. The disarm path correctly extracts both. Asymmetric.

**Impact:** Arm failures that aren't force-arm-eligible lose diagnostic info.
The notification says "Panel rejected operation" with no details.

**Fix:** In `_check_arm_status_once`, if `res == "ERROR"` and NON_BLOCKING
check fails, raise `OperationFailedError` directly with error details (like
`_check_disarm_status_once` does).

---

## MEDIUM Findings

### M1. `_last_proto` empty on first arm after startup
**Source:** Security A2, State Mgmt A2
**Previously flagged:** post-v0.7.0 review M10 — still unfixed
**Scope:** `client.py:169, 684, 860`

`_last_proto` initialized to `""`, never populated from coordinator polls.
First arm/disarm sends `currentStatus=""` to the API. The API tolerates it
today but the contract is wrong.

**Fix:** In `_async_update_data`, after parsing proto code, set
`self.client._last_proto = status.status`. One line.

---

### M2. Force context expiry only checked at poll boundaries
**Source:** Security A3, State Mgmt (implied)
**Previously flagged:** post-v0.7.0 review M2 — still unfixed
**Scope:** `alarm_control_panel.py:132-143`

Force context has 120s TTL but is only checked in `_handle_coordinator_update`.
With 300s poll interval, force context could live ~420s.

**Fix:** Use `hass.helpers.event.async_call_later(120, callback)` for
deterministic expiry.

---

### M3. UnexpectedStateError in polling doesn't create persistent notification
**Source:** Security A5
**Previously flagged:** post-v0.7.0 review M11 — still unfixed
**Scope:** `coordinator.py:219-221`

Unknown proto code → `_LOGGER.error` + `UpdateFailed`. No persistent
notification. This is the single most security-critical event the integration
can encounter — relying on log visibility is insufficient.

**Fix:** Create persistent notification before raising `UpdateFailed`.

---

### M4. Force context expiry doesn't dismiss notification or notify listeners
**Source:** State Mgmt A1
**Scope:** `alarm_control_panel.py:133-144`

When force context expires via the 120s timeout in `_handle_coordinator_update`:
(1) persistent notification never dismissed, (2) `async_update_listeners()` not
called so force-arm buttons may stay available for one extra poll cycle.

**Fix:** Fire-and-forget `_dismiss_notification()` via `async_create_task()`
from the sync callback. Add `coordinator.async_update_listeners()`.

---

### M5. Poll suppression during force context hides third-party state changes
**Source:** State Mgmt A5
**Scope:** `alarm_control_panel.py:133-146`

While force context is active, ALL state updates are suppressed. If someone
arms via the Verisure app or physical panel, HA stays stuck at ARMING for up
to 120s.

**Fix:** Compare coordinator's current alarm_state against the pre-exception
state (DISARMED). If coordinator reports something other than DISARMED, clear
force context and update.

---

### M6. `camera_entities: list[object]` with hasattr duck typing
**Source:** Core S1, UI S6, Cross S3, Boundaries A6, HA A7
**Scope:** `coordinator.py:184-196`

Duck-typed with `hasattr(entity, "refresh_from_coordinator")` + `type: ignore`.
The project already has the `ForceArmable` Protocol — same pattern should apply.

**Fix:** Define `CameraRefreshable(Protocol)`, type as `list[CameraRefreshable]`.

---

### M7. `capture_single_camera` no try/finally for camera_capturing flag
**Source:** Core S8, State Mgmt A6
**Scope:** `coordinator.py:349-362`

If `_overlay_text` raises an unexpected exception, `camera.zone_id` stays in
`camera_capturing` permanently. No `try/finally` on the success path.

**Fix:** Wrap in `try/finally` that always calls `camera_capturing.discard()`.

---

### M8. Entity bypasses coordinator to call client directly
**Source:** Boundaries A1
**Scope:** `alarm_control_panel.py:170-215`

`VerisureAlarmPanel` calls `coordinator.client.arm()` directly, bypassing the
coordinator's exception handling. Two parallel exception-handling paths exist.

**Fix:** Add arm/disarm methods to coordinator that mirror the poll path's
session-recovery logic. Or at minimum catch `SessionExpiredError` (→ H1).

---

### M9. PanelError: all fields default to None
**Source:** Client S6
**Scope:** `models.py:213-222`

Any JSON object (even `{}`) parses as valid `PanelError`. Force-arm detection
checks `error.type == "NON_BLOCKING"` — would be `None` on malformed error,
so force-arm path skipped (fail-secure), but diagnostics lost silently.

**Fix:** Make `code` and `type` required fields.

---

### M10. Undeclared `currentStatus` variable in disarm mutations
**Source:** Client S3
**Scope:** `client.py:860,913`; `graphql.py:134-151`

Disarm sends `currentStatus` in variables but the GraphQL query doesn't
declare `$currentStatus`. Server silently ignores it. Arm mutations correctly
declare it. Asymmetric.

**Fix:** Add `$currentStatus: String` to disarm queries, or stop sending it.

---

### M11. `_get_exceptions` returns empty list on unexpected res
**Source:** Client S5
**Scope:** `client.py:830-835`

Non-WAIT/non-OK response → warning log + empty list. `ArmingExceptionError`
still raised (arm blocked — good), but zero diagnostic info about zones.

**Fix:** Include unexpected `res` value in the error context.

---

### M12. `validate_device` re-parses response after `_check_graphql_errors`
**Source:** Client S10
**Scope:** `client.py:485-492`

Two code paths inspect the same response for errors. The "don't raise on OTP"
logic in `_check_graphql_errors` exists only for this caller. Fragile coupling.

**Fix:** Extract OTP detection or have `_check_graphql_errors` return OTP data.

---

### M13. services.yaml declares entity_id but handlers ignore it
**Source:** HA Patterns A3
**Scope:** `services.yaml`, `__init__.py`

`services.yaml` declares `entity_id` as required for `force_arm`/
`force_arm_cancel`, but handlers use `vol.Schema({})` and find the first
alarm entity by iterating coordinators. The field is ignored.

**Fix:** Remove `entity_id` from `services.yaml` (honest), or update
handlers to use it (correct-for-scale).

---

### M14. `hass.data[DOMAIN]` instead of `ConfigEntry.runtime_data`
**Source:** HA Patterns A1
**Scope:** `__init__.py`, all platform files

Old-school `hass.data[DOMAIN][entry.entry_id]` pattern. Modern HA uses
`ConfigEntry.runtime_data` with typed generic for type-safe coordinator
lookup.

**Fix:** Define `type VerisureConfigEntry = ConfigEntry[VerisureCoordinator]`,
use `entry.runtime_data`.

---

### M15. Reauth strings missing from strings.json
**Source:** HA Patterns A8
**Scope:** `config_flow.py`, `strings.json`

`reauth_confirm`, `reauth_2fa_phone`, `reauth_2fa_code` steps have no
`strings.json` entries. User sees raw field names during reauth.

**Fix:** Add string definitions for all reauth steps.

---

### M16. Fire-and-forget tasks not cancelled on unload
**Source:** HA Patterns A10
**Scope:** `__init__.py:77-101`, `camera.py:46`

Dashboard setup and thumbnail refresh tasks not cancelled in
`async_unload_entry`. Could generate confusing logs post-unload.

**Fix:** Use `config_entry.async_create_task(hass, coro)` — automatically
cancelled on entry unload.

---

### M17. async_step_reauth uses .get() with fallback on required data
**Source:** Core S6
**Scope:** `config_flow.py:230`

`entry_data.get(CONF_USERNAME, "")` — if the key is missing, that's a data
integrity bug. Surface it, don't paper over it.

**Fix:** Use `entry_data[CONF_USERNAME]`.

---

### M18. Phantom CameraGroup with empty-string sentinel
**Source:** UI S3
**Scope:** `dashboard.py:172-173`

Uses `camera_entity=""` as sentinel instead of `None`. Fragile truthiness check.

**Fix:** Use `camera_entity: str | None = None`, check `is not None`.

---

### M19. Missing test coverage: validate_device, capture_image
**Source:** Cross S6
**Scope:** `tests/`

Critical client operations lack tests: full OTP flow, multi-step capture
with timeout, session auto-refresh during arm/disarm.

**Fix:** Add test classes for these flows.

---

### M20. No test for unknown proto code through full arm chain
**Source:** Cross S12
**Scope:** `tests/`

Unknown `protomResponse` propagation from `_poll_operation` → `arm()` →
entity is untested. The fail-secure behavior is correct but unproven.

**Fix:** Add test where `_alarm_status_complete("Z")` verifies
`UnexpectedStateError` propagates.

---

### M21. Test state_mapping duplicates production mapping logic
**Source:** Cross S1
**Scope:** `tests/test_state_mapping.py:23-31`

Local `map_alarm_state()` reimplements production `_STATE_MAP`. If production
mapping changes, tests still pass against stale copy.

**Fix:** Import production code or document the intentional isolation.

---

## LOW Findings (summary)

| # | Title | File |
|---|-------|------|
| L1 | ForceArmContext.mode is bare `str`, should be StrEnum | coordinator.py:111 |
| L2 | `_try_full_image` silently swallows SessionExpiredError without logging | coordinator.py:372-376 |
| L3 | `_handle_coordinator_update` force context state not explicitly set | alarm_control_panel.py:134-146 |
| L4 | `_retries_left` recursive default parameter | coordinator.py:310 |
| L5 | `_fetch_thumbnail` falls back to `datetime.now()` on unparseable timestamp | coordinator.py:436-438 |
| L6 | Installation constructed with empty-string placeholders | coordinator.py:154-167 |
| L7 | async_step_reconfigure uses `.get()` with fallback | config_flow.py:403 |
| L8 | `@dataclass` in dashboard.py instead of Pydantic BaseModel | dashboard.py:27-44 |
| L9 | `async_unregister_dashboard` except Exception too broad | dashboard.py:70-71 |
| L10 | `async_setup_dashboard` except Exception too broad | dashboard.py:86-104 |
| L11 | Silent None-to-"" coercion on device_id/unique_id in dashboard | dashboard.py:158,171 |
| L12 | Dead operation name "RefreshLogin" in header logic | client.py:315 |
| L13 | `set_poll_params` uses `= None` defaults | client.py:173-180 |
| L14 | `_generate_request_id` non-zero-padded timestamps | client.py:336-342 |
| L15 | `GraphQLError.message` defaults to empty string | responses.py:265 |
| L16 | `Thumbnail` model nearly all fields default to None | models.py:336-350 |
| L17 | Hardcoded expected strings in test_state_mapping | test_state_mapping.py |
| L18 | Coordinator opts.get() defaults duplicated in 3 places | coordinator.py, __init__.py |

---

## Previously Flagged Items — Status

| Prior ID | Issue | Status |
|----------|-------|--------|
| M2 | Force context expiry only at poll boundaries | **Still unfixed** → M2 |
| M3 | ARMING state lie during force-arm window | **Still unfixed** → H2 |
| M10 | `_last_proto` empty on startup | **Still unfixed** → M1 |
| M11 | UnexpectedStateError no persistent notification | **Still unfixed** → M3 |

---

## Verified Secure Behaviors

1. **Unknown proto codes crash loud** — `parse_proto_code()` raises
   `UnexpectedStateError`, never defaults. Startup assertion covers all states.
2. **Timeout behavior is fail-secure** — `OperationTimeoutError` assumes
   previous state. Callers revert to `_update_alarm_state()`.
3. **No credential logging** — no `_LOGGER` call includes passwords or tokens.
4. **No GraphQL injection** — parameterized variables, never string interpolation.
5. **Session expiry triggers re-auth in polls** — `SessionExpiredError` →
   re-login in coordinator (gap only in entity arm/disarm paths → H1).
6. **Force-arm context scoped and expires** — carries one-time API token,
   120s TTL, cleared on success/failure/cancel.
7. **Concurrent arm/disarm protected by asyncio.Lock** — `_arm_lock` prevents
   simultaneous operations.
8. **Token refresh lock-protected** — `_auth_lock` prevents concurrent callers
   racing on refresh.
9. **Disarm errors surfaced** — error code and type extracted and raised.
10. **Entity attributes expose no secrets** — only zone aliases, flags,
    timestamps, device types.

---

## Trajectory Review

### What we built
A complete HA replacement for the Verisure Italy mobile app: arm/disarm with
force-arm exception flow, camera capture with overlay and retry, auto-generated
dashboard, full config flow with 2FA and reauth. The API client is a standalone
typed GraphQL library with Pydantic models at every boundary.

### Does it serve the mission?
Yes. The two-axis state machine is rigorous. Fail-secure behavior is correct
at the protocol level. Unknown states crash. Credentials are protected. The
codebase is dramatically cleaner than v0.6.0 — the previous review found 3
CRITICALs and 7 HIGHs; this review finds 0 CRITICALs and 4 HIGHs.

### What's missing
1. **Session recovery in command paths** (H1) — the biggest gap. The poll path
   handles session expiry; the arm/disarm path does not.
2. **Honest state during force-arm** (H2) — entity lies about state for up to
   120s. Automations can't react to the real DISARMED state.
3. **Duplicate installation guard** (H3) — trivial fix, prevents a class of
   confusing failures.

### Risk check
- **WAF/rate limiting:** no WAF-specific retry or backoff visible. The
  `WAFBlockedError` is caught but there's no exponential backoff.
- **API drift:** Pydantic models with `None` defaults on error responses
  (M9, L16) mean API changes could be silently accepted.
- **HA upgrade fragility:** Dashboard uses Lovelace internals (known, guarded).
  `hass.data` pattern is outdated but functional.

### Direction recommendation
Fix the 4 HIGHs first — they're all bounded, concrete changes. Then address
the 4 still-unfixed items from the prior review (M1-M3 + H2). The MEDIUM
findings are split between real correctness gaps (M4-M5 force context, M7
camera flag, M9-M11 protocol) and modernization debt (M14 runtime_data, M15
strings, M16 task cleanup). Correctness first, modernization second.
