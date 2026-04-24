# Codebase Review — 2026-04-24

Full-surface review run via `/verisure-review`. 8 parallel agents:
4 line-level (client, integration core, integration UI, tests+cross)
and 4 architecture (boundaries, state, HA patterns, security).
Current state: post-v0.9.0 (CP03 S1 — panel resolver + armed-transition
guard + cross-perimeter guard shipped).

## Severity summary

| Scope              | CRIT | HIGH | MED | LOW | Total |
|--------------------|------|------|-----|-----|-------|
| Client lib         | 0    | 0    | 2   | 0   | 2     |
| Integration core   | 0    | 0    | 1   | 1   | 2     |
| Integration UI     | 0    | 2    | 5   | 1   | 8     |
| Tests + cross      | 0    | 1    | 3   | 1   | 5     |
| Arch: Boundaries   | 0    | 0    | 4   | 3   | 7     |
| Arch: State        | 0    | 0    | 6   | 2   | 8     |
| Arch: HA patterns  | 0    | 1    | 5   | 5   | 11    |
| Arch: Security     | 0    | 0    | 1   | 6   | 7     |
| **Total (deduped)**| **0**| **2**| **19**| **13**| **34** |

No CRITICAL findings. Two HIGH. Remainder is medium/low tech debt.

Dedup applied: dashboard bare `except Exception` appears in 4 agents → one
row. Dashboard `@dataclass` appears in UI + core → one row. Installation
empty-string construction appears in boundaries + security → one row.

---

## Trajectory review

**What we built.** The mission ("replace the Verisure Italy mobile app
with a Home Assistant integration") is substantially delivered. The
integration covers: login + JWT/capability refresh, status polling,
arm/disarm with protocol-accurate two-axis state, force-arm flow with
120s expiring context, camera snapshots with overlay, automation-ready
services, config-flow onboarding, and fail-secure behavior on unknown
proto codes. The v0.9.0 panel resolver + gate (`SUPPORTED_PANELS`) makes
it safe to ship to HACS users with unverified panels — they get a
diagnostic probe rather than a silent misfire.

**Does it serve the mission?** Yes. A user on an SDVECU panel can drop
the Verisure app today, use HA for arm/disarm/camera, and gain
automations the app cannot do (scheduled arm, actionable disarm notifs,
multi-camera grid). The fail-secure stance is real — unknown state
crashes loud, disarm failure preserves armed assumption, poll crash
keeps last known state.

**What's missing.** (a) Audit trail density for force-arm is thin —
success path only logs zone count, not the full server decision
context. (b) Broad coupling between integration entities and client
Pydantic models (`AlarmState`, `ProtoCode`, `ZoneException`) — the
"parse at boundary" rule is honored at the HTTP layer but the
integration re-exports client types as entity state shape. (c) The
dashboard feature uses unstable HA Lovelace internals — the `try/except
Exception` around `LovelaceStorage` is a known risk accepted with no
version-pin and no CHANGELOG-driven HA-compat tracking. (d) A handful
of `@dataclass` holdovers violate the "Pydantic only" rule (resolver,
dashboard helper types).

**Risk check.** No disarm-leaking paths found. No fail-open fallbacks
to DISARMED. No credentials in logs / diagnostics / exceptions.
`SUPPORTED_PANELS` gate is enforced at HA entity layer — only weak
point is that `CommandResolver` itself trusts `PANEL_FAMILIES` (wider
set), so a library-level caller could bypass the allowlist. Not a
concern for HA users; worth tightening if anyone adopts the client lib
out of tree. Force-arm context is short-lived (120s) and not persisted
across restart, which is the correct conservative choice.

**Direction (2-3 sentences).** Project is in a healthy shape to
broaden panel coverage; the next meaningful lever is (i) replacing the
remaining `@dataclass` usages with Pydantic to close the type-discipline
loop, and (ii) tightening the force-arm audit trail into a single
structured log entry so security incidents are reconstructible from HA
logs alone. Dashboard/Lovelace-internals risk deserves a documented
HA-version compatibility matrix before that subfeature grows.

---

## HIGH severity findings

### H1. Dashboard setup swallows every `Exception`, hiding real bugs
**Files:** `custom_components/verisure_italy/dashboard.py:70`, `:86`
**Agents:** UI S1-S3, tests+cross S2, HA patterns A2
**Problem:** Two bare `except Exception:` blocks in
`async_unregister_dashboard` and `async_setup_dashboard` log the error
and continue. The comment frames this as "dashboard failure never
prevents integration load," but the blanket catch masks unrelated
programming errors (AttributeError from a refactor, TypeError from a
wrong cast) in the same bucket as expected HA Lovelace API drift.
**Fix:** Narrow to the specific Lovelace-drift exceptions actually
seen (`AttributeError`, `KeyError`, `TypeError`, `ImportError`). Let
anything else crash. Also worth documenting which HA versions' Lovelace
internals are known to work, since `LovelaceStorage` is an unstable API.

### H2. Coordinator reconstructs `Installation` with empty-string metadata
**Files:** `custom_components/verisure_italy/coordinator.py:160-173`
**Agents:** Boundaries A1, Security A7
**Problem:** Instead of persisting the real `Installation` from
`list_installations()` during config flow, the coordinator rebuilds it
at runtime from three scalars (`numinst`, `alias`, `panel`) and stuffs
empty strings into every other field. The client model intentionally
softens those fields to `| None = None` to survive Verisure schema
drift — but the coordinator immediately un-softens them to `""`. This
is both a boundary violation (integration fabricates a client-domain
object) and a consistency bug (two agents flagged the contradiction).
**Fix:** Store the real `Installation` from config flow in
`entry.data` (or refetch at coordinator init) and keep it as the single
source of truth. Do not synthesize client-domain objects integration-side.

---

## MEDIUM severity findings

### M1. `@dataclass` used where CLAUDE.md mandates Pydantic
**Files:** `verisure_italy/resolver.py:89` (`CommandResolver`),
`custom_components/verisure_italy/dashboard.py:26,34`
(`CameraGroup`, `DashboardEntities`)
**Agents:** Client S1, Integration core S1, tests+cross (implicit)
**Fix:** Convert to `BaseModel` with `model_config = ConfigDict(frozen=True)`.

### M2. Naked `datetime.now()` without `tz=UTC`
**Files:** `verisure_italy/client.py:544`, `:642`
**Agents:** Client S2
**Problem:** The rest of the codebase uses `datetime.now(tz=UTC)`;
these two slip through, creating tz-naive timestamps in request ID
generation and `_login_timestamp`.
**Fix:** Add `tz=UTC` to both.

### M3. Race: poll can overwrite state mid arm/disarm
**Files:** `custom_components/verisure_italy/alarm_control_panel.py:165-176`,
`coordinator.py`
**Agents:** State A2
**Problem:** `_arm_lock` prevents the entity's `_handle_coordinator_update`
from writing state while arming, but it does not stop the coordinator
from issuing the poll itself. A poll that completes before the arm
command lands can refresh `coordinator.data` with stale DISARMED state;
UI recovers on the next tick but can flicker.
**Fix:** Add a coordinator-level `_suppress_updates` flag set by the
entity before arm/disarm and cleared on completion, gating
`_async_update_data` entirely.

### M4. Force-arm context survives panel-state change
**Files:** `alarm_control_panel.py` (`_set_force_context`,
`_expire_force_context`), `coordinator.py` (`force_context`)
**Agents:** State A3
**Problem:** Context stores `reference_id`/`suid` for 120s. If the
user closes the open zone within that window and the next poll shows
the alarm already armed (panel moved on), the stale context is still
live. Pressing "Force Arm" now sends a reference_id the panel no longer
recognizes → fails with an opaque error.
**Fix:** In `_handle_coordinator_update`, if `force_context` is set but
fresh poll shows alarm already in the target armed state, clear the
context (no notification needed — success implied).

### M5. Force-context expiry path skips listener notify
**Files:** `alarm_control_panel.py:488-499` vs user-cancel path
**Agents:** State A1
**Problem:** Timer-driven `_expire_force_context()` clears coordinator
state but does not call `async_update_listeners()` — the user-cancel
`_clear_force_context()` does. Entities can show stale force-arm button
availability for one tick.
**Fix:** Unify both paths through a single helper that always notifies.

### M6. `OperationTimeoutError` reverts entity state even if the panel later completes
**Files:** `alarm_control_panel.py:294`, `client.py:1589-1596`
**Agents:** State A4
**Problem:** If the panel completes the operation T=61s but the
client timed out at T=60s, the entity has already reverted; the
coordinator's next poll sees the correct final state, but there's a
window where HA and panel disagree.
**Fix:** On `OperationTimeoutError`, set entity to UNKNOWN + trigger
immediate forced refresh rather than reverting to prior state.

### M7. Pydantic models leak from client into integration entities
**Files:** `coordinator.py:93-101` (`VerisureStatusData`), `:104-114`
(`ForceArmContext`)
**Agents:** Boundaries A3, A5
**Problem:** Coordinator data structure re-exports `AlarmState`,
`ProtoCode`, `ZoneException` from the client. Integration entities
reach into `.alias` etc. directly. Any change to client model shape
becomes a breaking change to every entity.
**Fix:** Introduce a thin integration-side translation layer, OR
formalize that these specific client models are stable public API
(documented contract, tested for shape stability).

### M8. `ValidationError` handled only in arm/disarm, not in other client calls
**Files:** `alarm_control_panel.py:302`, `:344`, `:398`
**Agents:** Boundaries A4
**Problem:** Arm/disarm wraps `(VerisureError, ValidationError)`, but
`force_arm_cancel`, `_check_panel_supported`, and camera discovery do
not. API schema drift causes graceful degradation in some paths and a
raw crash in others.
**Fix:** Either wrap uniformly, or centralize in a decorator / helper.

### M9. Coordinator auth-recovery classifies network errors as `UpdateFailed`
**Files:** `coordinator.py:257-262`
**Agents:** HA patterns A10
**Problem:** After `SessionExpiredError` triggers re-login, a
transient network error during `client.login()` is caught by the generic
`(APIConnectionError, ...)` handler and surfaces as `UpdateFailed`.
Actual auth failure is only raised as `ConfigEntryAuthFailed` on a
clean `AuthenticationError`.
**Fix:** Inside the re-login block, re-raise `AuthenticationError` /
`TwoFactorRequiredError` as `ConfigEntryAuthFailed`, but let network
errors retry without escalating.

### M10. Config flow does not handle network exceptions
**Files:** `config_flow.py:84-98`
**Agents:** HA patterns A9
**Problem:** `AuthenticationError` is mapped to a form error, but
`aiohttp.ClientError` / `asyncio.TimeoutError` propagate uncaught → HA
shows raw traceback rather than a user-facing "network error."
**Fix:** Catch `(aiohttp.ClientError, asyncio.TimeoutError,
APIConnectionError)` and map to `errors={"base": "cannot_connect"}`
with matching string in `strings.json`.

### M11. Probe path bare `except Exception`
**Files:** `alarm_control_panel.py:202`
**Agents:** Tests+cross S3
**Fix:** Narrow to `(APIConnectionError, APIResponseError, ValidationError)`.

### M12. Dashboard builder uses `dict[str, Any]` for Lovelace cards
**Files:** `dashboard.py:179, 183, 190, 200, 210`
**Agents:** UI S4, S5
**Problem:** Lovelace card configs are bare `dict[str, Any]` — typos
in keys (`entiy`, `typ`) compile clean.
**Fix:** Define TypedDict per card type (`TileCard`, `PictureEntityCard`,
`ConditionalCard`).

### M13. Camera explicit double-init
**Files:** `camera.py:66-67`
**Agents:** HA patterns A3
**Problem:** Calls `super().__init__(coordinator)` AND
`Camera.__init__(self)`. Relies on MRO quirks; fragile.
**Fix:** Use only `super().__init__()` and trust C3 linearization, or
document the explicit call with the exact HA version behavior that
requires it.

### M14. `_overlay_text` loads font on every image
**Files:** `coordinator.py:69-90`
**Agents:** UI S8
**Fix:** Load font once at coordinator init (in executor), reuse.

### M15. Race: `resolver.resolve` raises `ValueError` when poll wins against arm
**Files:** `client.py:913-916`, `resolver.py:115`
**Agents:** State A7
**Problem:** Entity sees old state, proceeds to arm; poll updates
`_last_proto` to target state before resolver runs; resolver raises
`ValueError: current == target`; entity catches only `VerisureError` /
`ValidationError`, so raw traceback surfaces.
**Fix:** Either catch `ValueError` at entity layer and treat as
no-op, or update `_last_proto` only under `_arm_lock`.

### M16. Timestamp parse fallback silently overwrites API timestamp
**Files:** `coordinator.py:509-513`
**Agents:** UI S9
**Problem:** Invalid API timestamp → `datetime.now()` fallback, silent.
Displayed overlay becomes wrong without any log line.
**Fix:** Log WARNING on parse fail; or validate timestamp shape in
Pydantic response model so failure raises at boundary.

### M17. No dedicated audit trail for force-arm success
**Files:** `alarm_control_panel.py` (`async_force_arm`)
**Agents:** Security A1
**Problem:** Successful force-arm only logs zone count + names. Full
server response, `reference_id`, `suid`, resulting proto code are
scattered or only emitted on failure. CLAUDE.md asks for "every
arm/disarm action logged with timestamp, source, command, proto
response, resulting state."
**Fix:** One structured log entry per force-arm attempt (success OR
failure) with full fields.

### M18. `# type: ignore[reportIncompatibleVariableOverride]` without justification
**Files:** `alarm_control_panel.py:101`, `button.py:50,94,150,211`,
`camera.py:53`
**Agents:** HA patterns A1
**Fix:** Add inline comment on each explaining the MRO reason.

### M19. `homeassistant` minimum version missing from manifest
**Files:** `manifest.json`
**Agents:** HA patterns A11
**Problem:** Integration relies on HA 2026.4+ thread-safety patterns
but does not declare a minimum HA version.
**Fix:** Add `"homeassistant": "2026.4"` to manifest.

### M20. Test `_make_panel_entity` bypasses `__init__`
**Files:** `tests/test_alarm_panel_gate.py:52`
**Agents:** Tests+cross S5
**Problem:** `__new__()` skips entity init, so `__init__` changes
don't break the gate tests.
**Fix:** Test full init path in at least one gate test.

---

## LOW severity findings (condensed)

- **L1.** `_services_cache` populated outside `_auth_lock` — race window
  on first arm+poll (Security/State A8)
- **L2.** Two-axis state collapsed on entity without exposing
  `interior_mode`/`perimeter_mode` attributes (State A5)
- **L3.** `main(argv: list[str] | None = None)` violates "no defaults"
  rule — argparse convention, document or wrap (tests+cross S4)
- **L4.** `CommandResolver` panel check reads `PANEL_FAMILIES` not
  `SUPPORTED_PANELS` — library-level callers could bypass allowlist
  (Security A4). Add `assert SUPPORTED_PANELS == set(PANEL_FAMILIES)` test.
- **L5.** `StateNotObservedError` not listed in arm/disarm docstring
  Raises section (Boundaries A6)
- **L6.** Disarm-without-permission message is opaque — parse
  `error_no_response_to_request` and tell the user (Security A6)
- **L7.** Missing `_handle_coordinator_update` not an issue — all three
  entity types implement it correctly (positive finding, HA patterns A8)
- **L8.** `services.yaml` missing `fields: {}` for parameter-free
  services (HA patterns A12)
- **L9.** Force-context cleared silently on reload — log a message
  (Security A3)
- **L10.** Unique-ID separator ambiguity if `numinst` contains
  underscores — cosmetic (HA patterns A7)
- **L11.** `_async_expire_force_context` nested inside
  `_set_force_context` — lift for unit testability (HA patterns A13)
- **L12.** Alarm panel `_on_force_context_expired(_now)` param missing
  type annotation (UI S6)
- **L13.** Inconsistent debug logging on `_try_full_image` vs
  `_fetch_thumbnail` failure paths (integration core S2)

---

## Top 5 action items (ranked)

1. **Merge-window fix:** Convert `CommandResolver`, `CameraGroup`,
   `DashboardEntities` to Pydantic (M1). Single PR, tight blast radius.
2. **Fix installation rebuild** (H2) — persist real `Installation` in
   config entry, drop the empty-string synthesis. Cleans up two agents'
   findings and matches the "parse at boundary" principle.
3. **Audit trail PR** (M17) — one structured log line per force-arm
   success AND failure. Pays off the first time the user needs to
   reconstruct an incident.
4. **Dashboard exception narrowing** (H1) — swap `except Exception` for
   the real LovelaceStorage drift exceptions; document the HA-version
   compatibility matrix in CHANGELOG.
5. **Arm/disarm race hardening** (M3 + M15) — coordinator-level
   suppress flag + catch `ValueError` in resolver path. Eliminates the
   flicker class of bugs.

---

*Generated by `/verisure-review` — 8 parallel agents, deduped across
overlapping scopes.*
