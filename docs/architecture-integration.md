# HA Integration Architecture

`custom_components/verisure_italy` — Home Assistant custom component.

## Module Structure

```
custom_components/verisure_italy/
├── __init__.py           # Entry point, service registration
├── alarm_control_panel.py # Alarm entity + force-arm logic
├── button.py             # Capture + force-arm button entities
├── camera.py             # Camera entities (on-demand capture)
├── config_flow.py        # Config + options + reconfigure flows
├── coordinator.py        # DataUpdateCoordinator (polling + shared state)
├── dashboard.py          # Auto-managed Lovelace dashboard
├── const.py              # Domain, config keys, defaults
├── manifest.json         # HACS/HA metadata
├── strings.json          # UI strings
└── translations/en.json  # English translations
```

## Entity Relationship

```mermaid
graph TD
    subgraph Coordinator
        VC[VerisureCoordinator]
        FC[force_context]
        AE[alarm_entity ref]
    end

    subgraph "Alarm Device"
        AP[AlarmControlPanel<br/>alarm_control_panel.verisure_alarm]
        FA[ForceArmButton<br/>button.verisure_force_arm]
        CA[CancelForceArmButton<br/>button.verisure_cancel_force_arm]
        CAP[CaptureAllButton<br/>button.verisure_capture_all_cameras]
    end

    subgraph "Camera Devices (×N)"
        CAM[Camera<br/>camera.verisure_*]
        CB[CaptureButton<br/>button.verisure_*_capture]
    end

    VC -->|data updates| AP
    VC -->|data updates| FA
    VC -->|data updates| CA
    VC -->|data updates| CAM
    AP -->|registers self| AE
    AP -->|reads/writes| FC
    FA -->|reads| FC
    FA -->|calls async_force_arm| AP
    CA -->|calls async_force_arm_cancel| AP
    CAP -->|async_capture_all_cameras| VC
    CB -->|capture_single_camera| VC
```

## Polling and State Updates

```mermaid
sequenceDiagram
    participant HA as Home Assistant
    participant CO as Coordinator
    participant API as Verisure API
    participant AL as Alarm Entity
    participant BT as Force Arm Buttons

    loop Every 5s (configurable)
        CO->>API: xSStatus (passive poll)
        API-->>CO: alarm state + exceptions
        CO->>AL: _handle_coordinator_update()
        CO->>BT: _handle_coordinator_update()
        AL->>HA: async_write_ha_state()
        BT->>HA: async_write_ha_state()
    end
```

## Arm with Force-Arm Flow

```mermaid
stateDiagram-v2
    [*] --> Disarmed

    Disarmed --> Arming: User taps Arm
    Arming --> Armed: API success
    Arming --> WaitingForceArm: Open zones detected

    state WaitingForceArm {
        [*] --> ButtonsVisible
        note right of ButtonsVisible
            Force Arm + Cancel buttons
            appear on dashboard
        end note
    }

    WaitingForceArm --> Armed: User taps Force Arm
    WaitingForceArm --> Disarmed: User taps Cancel
    WaitingForceArm --> Disarmed: 2min expiry

    Armed --> Disarming: User taps Disarm
    Disarming --> Disarmed: API success
```

## State Suppression During Operations

Two layered guards prevent a background poll from racing a user-driven
mutation. The entity-layer lock stops the update-handler from writing
stale state; the coordinator-layer flag stops the poll itself from
even fetching fresh data (so no stale snapshot is ever committed to
`coordinator.data` mid-transition).

| Guard | Where | When active | Effect |
|-------|-------|-------------|--------|
| `_arm_lock` | alarm entity | Around every arm/disarm/force-arm call | `_handle_coordinator_update()` returns early — no state write |
| `suppress_updates()` | coordinator | Same scope as `_arm_lock` (paired via `async with self._arm_lock, self.coordinator.suppress_updates():`) | `_async_update_data()` short-circuits to cached snapshot — the client isn't called at all |

Without these, the coordinator's 5-second poll could read the panel's
real state (e.g. DISARMED) during an arm operation and write it,
causing a visible state flicker and triggering automations.

`force_context` (a separate piece of state on the coordinator) is
not a guard — it's a data carrier for the 120s window between an
`ArmingExceptionError` and the user's decision (force-arm or cancel).
Its lifecycle is described below.

## Force Context Lifecycle

```mermaid
graph TD
    SET["_set_force_context()<br/>→ stores on coordinator<br/>→ async_update_listeners()<br/>→ buttons become available"]
    CLEAR["_clear_force_context()<br/>→ async_update_listeners()<br/>→ buttons become unavailable<br/>→ alarm state updated"]
    EXPIRE["_expire_force_context()<br/>→ silent clear, no listeners<br/>→ lets async_request_refresh()<br/>notify with fresh data"]

    AE[ArmingExceptionError] -->|exception path| SET
    FA[Force arm success] -->|success path| EXPIRE
    ARM[Normal arm success] -->|success path| EXPIRE
    DIS[Disarm success] -->|success path| EXPIRE
    CANCEL[Cancel button] -->|cancel path| CLEAR
    FAIL[Force arm failure] -->|error path| CLEAR
    TIMEOUT[2min expiry] -->|timeout| CLEAR
```

**Why two clear methods?**

- `_clear_force_context()` calls `async_update_listeners()` — buttons
  disappear immediately. Used in cancel/error paths where coordinator
  data is the correct state to display.

- `_expire_force_context()` clears silently. Used in success paths
  where the coordinator data is stale (hasn't polled yet). The
  following `async_request_refresh()` polls fresh data and notifies
  all entities. Without this split, the alarm would briefly show
  DISARMED (stale) before showing ARMED (fresh).

## Camera Capture

```mermaid
graph LR
    subgraph "Capture All (parallel)"
        C1[Camera 1<br/>t=0s]
        C2[Camera 2<br/>t=2s]
        C3[Camera 3<br/>t=4s]
        CN[Camera N<br/>t=2×N s]
    end

    C1 --> API1[API call ~13s]
    C2 --> API2[API call ~13s]
    C3 --> API3[API call ~13s]
    CN --> APIN[API call ~13s]

    API1 -->|fail| R1[Retry 3s backoff]
    R1 -->|fail| R2[Retry 6s backoff]
    R2 -->|fail| FAIL[Give up]
```

Cameras launch 2 seconds apart via `asyncio.gather` with staggered
`asyncio.sleep`. Each camera retries up to 2 times with exponential
backoff (3s, 6s). A `_capture_lock` prevents concurrent capture
rounds.

## Dashboard

The integration self-registers a Lovelace dashboard panel in the
sidebar using `frontend.async_register_built_in_panel`. The dashboard
config is rebuilt from discovered entities on every integration load.

The dashboard is removed when the integration is unloaded.

**Note:** This uses `LovelaceStorage` internals. If a HA update
breaks it, the integration continues to work — only the auto-generated
dashboard is affected.

## Config Flow

```mermaid
graph TD
    U[User step<br/>username + password] --> L{Login}
    L -->|success| I[Installation picker]
    L -->|2FA required| P[Phone picker]
    P --> C[SMS code entry]
    C --> L
    I --> DONE[Entry created]

    R[Reconfigure step<br/>new credentials] --> RL{Login}
    RL -->|success| RELOAD[Reload + abort]
    RL -->|2FA required| RP[Phone picker]
    RP --> RC[SMS code entry]
    RC --> RL
```

Options flow allows changing poll interval, timeout, and delay
without restart — applied live via `_async_options_updated`.

## Security Model

### This is security software

The alarm system protects a physical space. One wrong behavior =
disarmed alarm = intrusion. Every design decision prioritizes
correctness over convenience.

### Fail-secure design

| Scenario | Behavior |
|----------|----------|
| Unknown alarm state | `UnexpectedStateError` + notification. Never defaults to DISARMED. |
| Arm/disarm timeout | `OperationTimeoutError`. Entity state goes UNKNOWN and a forced refresh resolves it from the real panel — we do NOT guess the prior state. (`OperationFailedError`, where the panel explicitly rejected the command, is unambiguous and does revert to prior.) |
| Poll failure | `UpdateFailed`. Last known state preserved. |
| Force context expired | Reverts to coordinator data. Buttons disappear. |
| Panel armed via another path | Stale `force_context` auto-evicts on the next coordinator update (any non-DISARMED observation clears the pending token so a stale `reference_id` can't fire). |
| Unexpected exception in arm flow | `_arm_lock` + `suppress_updates()` released by the `async with` block. State recovers on the post-release `async_request_refresh()`. |

### Credential handling

- Credentials stored in HA's encrypted config entry storage
- Password used only for login, not retained after token acquisition
- Reconfigure flow updates credentials without exposing them in logs
- 2FA device registration is permanent per `device_id`

### API user roles

| Role | Arm behavior | Force arm | Risk |
|------|-------------|-----------|------|
| RESTRICTED | Arms regardless of open zones | N/A (no exceptions raised) | Sensors will trip |
| ADMIN | Raises `ArmingExceptionError` on open zones | Supported with `forceArmingRemoteId` + `suid` | User chooses to bypass |

The integration requires an ADMIN user for correct force-arm behavior.

### Attack surface

- **Network:** All communication over HTTPS to `customers.verisure.it`. No local panel access.
- **Authentication:** JWT tokens (EdDSA). Token refresh is lock-protected.
- **HA exposure:** Services (`force_arm`, `force_arm_cancel`, `capture_cameras`) require HA authentication. No unauthenticated endpoints.
- **Dashboard:** Read-only auto-generated panel. Cannot be edited from the UI.
