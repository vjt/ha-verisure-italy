# Codebase Review — 2026-04-04

Full review of ha-verisure-italy at commit `88d0434` (v0.6.0 + post-release).
8 parallel agents: 4 line-level (client, integration-core, integration-ui,
tests+cross) and 4 architecture (boundaries, state management, HA patterns,
security model).

## Summary Table

| Severity | Count | Key findings |
|----------|-------|-------------|
| CRITICAL | 3 | `_STATE_MAP.get()` fail-open, unhandled arm/disarm exceptions, missing reauth flow |
| HIGH | 7 | untyped `force_context`, `alarm_entity: Any`, blocking Pillow call, dead `UnexpectedStateError`, GraphQL error swallowing, camera entities not CoordinatorEntity, module-level global state |
| MEDIUM | 14 | (see below) |
| LOW | 10 | (see below) |

## CRITICAL Findings

### C1. `_STATE_MAP.get()` silently returns `None` for unmapped alarm states
**File:** `alarm_control_panel.py:104`
**Flagged by:** 5/8 agents (integration-core, tests+cross, boundaries, state-mgmt, security)

`_STATE_MAP.get(self.coordinator.data.alarm_state)` returns `None` when the
`AlarmState` is not in the map. Today, all 6 `ProtoCode` values are covered,
so this is unreachable. But the `.get()` without crash violates the project's
core principle: "Unknown states = ERROR, not defaults." If a new proto code
is added to `PROTO_TO_STATE` but not to `_STATE_MAP`, the alarm entity
silently shows "unknown" — not an error, not a notification.

**Fix:** Replace with `_STATE_MAP[alarm_state]` and catch `KeyError` to raise
`UnexpectedStateError` + persistent notification. Add a startup assertion
that `_STATE_MAP.keys() == set(PROTO_TO_STATE.values())`.

### C2. Arm/disarm paths don't catch all client exceptions
**File:** `alarm_control_panel.py:161-183, 196-207`
**Flagged by:** 2/8 agents (boundaries, security)

`_async_arm()` catches `ArmingExceptionError`, `OperationFailedError`, and
`OperationTimeoutError`. But `client.arm()` can also raise
`APIConnectionError`, `WAFBlockedError`, `SessionExpiredError`, and
`pydantic.ValidationError`. These propagate uncaught — `_arm_in_progress` is
cleared by `finally`, but `_attr_alarm_state` stays stuck at ARMING/DISARMING
until the next coordinator poll rescues it. No persistent notification.

**Fix:** Add catch-all for remaining `VerisureError` subtypes + `ValidationError`.
Revert state via `_update_alarm_state()`, log, and create persistent notification.

### C3. Missing reauth flow — `ConfigEntryAuthFailed` raised with no handler
**File:** `coordinator.py:180,184` + `config_flow.py` (missing `async_step_reauth`)
**Flagged by:** 1/8 agents (HA patterns)

The coordinator raises `ConfigEntryAuthFailed` on auth failures. HA expects
`async_step_reauth` in the config flow to handle this. No such step exists.
When credentials expire at runtime, HA shows "Reauthentication required" but
clicking the reauth button fails — user must remove and re-add the integration.

**Fix:** Add `async_step_reauth` + `async_step_reauth_confirm` to the config flow.

## HIGH Findings

### H1. `force_context: dict[str, Any] | None` — untyped bag across 3 modules
**File:** `coordinator.py:156`, `alarm_control_panel.py:275-282`, `button.py:177-182`
**Flagged by:** 6/8 agents

The force-arm context carries `reference_id`, `suid`, `mode`, `target`,
`exceptions`, `created_at` in a raw `dict[str, Any]`. Every consumer
accesses it by string keys with no type safety. A typo is a runtime
`KeyError` in the security-critical force-arm path.

**Fix:** Define a `ForceArmContext` Pydantic model with typed fields.

### H2. `coordinator.alarm_entity: Any = None` — untyped back-reference
**File:** `coordinator.py:159`, `button.py:187,227`
**Flagged by:** 6/8 agents

The coordinator stores a back-reference to the alarm entity typed as `Any`.
Buttons call `alarm.async_force_arm()` with zero compile-time checking.
A method rename would produce no pyright error.

**Fix:** Define a `ForceArmable` Protocol or use `TYPE_CHECKING` import.

### H3. `_overlay_text` called synchronously in `_try_full_image`
**File:** `coordinator.py:356`
**Flagged by:** 6/8 agents

Pillow image processing (decode, draw, encode) runs directly on the event
loop. The same function is correctly dispatched via `async_add_executor_job`
at two other call sites (lines 310, 403). This one was missed.

**Fix:** `await self.hass.async_add_executor_job(_overlay_text, ...)` — one-line fix.

### H4. `UnexpectedStateError` is dead code
**File:** `exceptions.py:60-72`, `models.py:90-98`, `coordinator.py:187-189`
**Flagged by:** 1/8 agents (security)

`parse_proto_code()` raises `ValueError` on unknown codes, but the
coordinator catches `UnexpectedStateError` (which is never raised). The
purpose-built exception with human-verification messaging never fires.

**Fix:** Have `parse_proto_code()` raise `UnexpectedStateError` instead of
`ValueError`.

### H5. `_check_graphql_errors` silently swallows errors with empty message
**File:** `client.py:243-271`
**Flagged by:** 1/8 agents (client)

If the API returns an error with `message=""` and `data=None`, all checks
fall through — the error is swallowed. The caller then tries to parse the
error response as success, producing an uncontrolled `ValidationError`.

**Fix:** Add a catch-all at the bottom: if `errors` is non-empty and no
branch handled it, raise `APIResponseError`.

### H6. Camera entities don't subclass `CoordinatorEntity`
**File:** `camera.py`, `button.py:49,93`
**Flagged by:** 2/8 agents (integration-ui, HA patterns)

`VerisureCamera`, `VerisureCaptureAllButton`, and `VerisureCaptureButton`
inherit only from their base HA classes, not `CoordinatorEntity`. They
don't receive coordinator update callbacks and won't reflect coordinator
failures (API down → buttons still show as available).

**Fix:** Add `CoordinatorEntity[VerisureCoordinator]` to their bases.

### H7. Module-level `_CAMERA_ENTITIES` mutable global state
**File:** `camera.py:25`
**Flagged by:** 3/8 agents (integration-ui, HA patterns, tests+cross)

Module-level dict never cleaned up on unload. Stale entity references
accumulate on reload. Violates "No global state, no singletons."

**Fix:** Store camera entity references on the coordinator. Clean up in
`async_shutdown`.

## MEDIUM Findings

### M1. `VerisureStatusData` and dashboard types use `@dataclass` instead of Pydantic
**Files:** `coordinator.py:92`, `dashboard.py:27,35`
**Flagged by:** 4/8 agents

### M2. Force context expiry coupled to poll interval
**File:** `alarm_control_panel.py:129-140`

120s TTL checked only at poll boundaries. If poll_interval > 120s, force
context persists beyond TTL. Fix: use `async_call_later` for deterministic
expiry.

### M3. Alarm shows ARMING during force-arm decision window
**File:** `alarm_control_panel.py:157-174`

After `ArmingExceptionError`, the entity stays ARMING until user force-arms
or cancels. Not a safety issue (extra attributes carry the real state), but
misleading UX.

### M4. `_get_alarm_entity` ignores `entity_id` from service call
**File:** `__init__.py:109-116`
**Flagged by:** 5/8 agents

Always grabs the first coordinator's alarm entity regardless of which
`entity_id` the caller specified.

### M5. Dashboard directly manipulates Lovelace internals
**File:** `dashboard.py`

Uses `hass.data["lovelace"]`, `LovelaceStorage`, `async_register_built_in_panel`
— all HA internals that could break on updates.

### M6. `aiohttp.ClientSession` created outside HA session management
**File:** `coordinator.py:121`

Should use `async_create_clientsession(hass)` or `async_get_clientsession(hass)`.

### M7. `pydantic.ValidationError` not caught anywhere in integration
**File:** All client method callers

API schema changes produce an unhandled `ValidationError` — should be caught
at the client boundary and re-raised as `APIResponseError`.

### M8. `_discover_entities` button matching is fragile substring matching
**File:** `dashboard.py:129-141`

Order-dependent `"force_arm"` in `unique_id` checks. Future button types
get misclassified.

### M9. `asyncio.gather(return_exceptions=True)` swallows exceptions silently
**File:** `coordinator.py:242-249`

Exceptions in results are counted as failures without logging.

### M10. Triplicated `proto_code`/`alarm_state`/`is_pending` properties
**File:** `models.py:176-198, 232-244, 258-269`

Three identical property sets across `OperationResult`, `ArmResult`, `DisarmResult`.

### M11. `check_request_images_status` treats ERROR as "done"
**File:** `client.py:1044-1045`

`res == "ERROR"` returns `True` (done) instead of raising.

### M12. `_get_exceptions` returns empty list on unexpected response / timeout
**File:** `client.py:824-836`

User sees "open zones:" with no zone details.

### M13. `Thumbnail` model: all fields default to `None`
**File:** `models.py:378-391`

Empty dict validates as Thumbnail. Parse-at-boundary violation.

### M14. `datetime.fromtimestamp()` without timezone
**File:** `client.py:358`

Naive datetimes, deprecated without `tz` since Python 3.12.

## LOW Findings

### L1. `DashboardEntities` uses `= None` defaults (dashboard.py:39-41)
### L2. `extra_state_attributes` returns `dict[str, Any]` (button.py:175)
### L3. `refresh_all_cameras` called after finally block (button.py:90,143)
### L4. `_discover_entities` creates `CameraGroup(camera_entity="")` for orphans (dashboard.py:138)
### L5. `_get_alarm_entity` has no return type annotation (__init__.py:109)
### L6. `capture_cameras` service registered without schema (__init__.py:152)
### L7. `ArmPanelResponse` model defined but never used (models.py:288)
### L8. `GraphQLError.message` defaults to empty string (responses.py:265)
### L9. `DisarmResult` missing `status` field that GraphQL query requests (models.py:246)
### L10. `PanelError` model: all fields default to None (models.py:208)

## Positive Findings

- **Credentials never logged** — no passwords, JWT tokens, or API keys appear in any log statement
- **`_arm_in_progress` suppression is correct** — asyncio cooperative scheduling guarantees no poll can interleave
- **Entity availability before first poll correctly handled** — `async_config_entry_first_refresh` runs before platform setup
- **RESTRICTED user role** — correctly relies on server-side enforcement

## Trajectory Review

**What we built:** A complete replacement for the Verisure Italy mobile app
in Home Assistant — alarm control (arm/disarm/force-arm with open zones),
camera capture (parallel with stagger and retry), auto-managed dashboard,
and a typed GraphQL client library.

**Does it serve the mission?** Yes. The core alarm control loop is sound.
The state machine is complete for the known protocol. Force-arm UX works
end-to-end. Camera capture is optimized. The integration is installable
via HACS and deployable via the custom skills.

**What's missing:**
- Reauth flow (C3) — expired credentials brick the integration
- Audit trail gaps (A7 from security agent) — arm/disarm log intent but not the panel's proto response
- Test coverage for camera capture and 2FA flows

**Risk check:** The three CRITICAL findings are all latent — they don't
affect current behavior but create fail-unsafe paths when the code
evolves. The HIGH findings are a mix of one real bug (H3, blocking Pillow)
and structural type-safety gaps that reduce pyright's ability to catch
regressions.

**Direction:** Fix C1 (one-line `.get()` → `[]`), C2 (exception handling),
and H3 (one-line executor fix) immediately — these are mechanical. Then
tackle C3 (reauth flow) and H1/H2 (ForceArmContext model + Protocol typing)
as the next iteration. The codebase is solid — these are hardening issues,
not structural problems.
