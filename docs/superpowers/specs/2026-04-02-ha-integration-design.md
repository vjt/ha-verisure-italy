# Home Assistant Integration Design — verisure_it

## Overview

Custom component for Home Assistant that controls a Verisure Italy alarm
system via the `verisure_api` client library. Replaces the Verisure mobile
app and the upstream Securitas Direct HACS plugin with a clean,
Italy-specific integration built on an E2E-validated API client.

This is security software. The design optimizes for correctness and
fail-secure behavior over convenience.

## File Structure

```
custom_components/verisure_it/
  manifest.json            # Integration metadata, dependencies
  __init__.py              # async_setup_entry / async_unload_entry
  const.py                 # DOMAIN, config keys, defaults
  config_flow.py           # Multi-step: credentials -> 2FA -> installation
  coordinator.py           # DataUpdateCoordinator polling xSStatus
  alarm_control_panel.py   # AlarmControlPanelEntity
  strings.json             # UI strings for config flow
  translations/
    en.json                # English translations
```

## State Mapping

Two-axis alarm model (interior x perimeter) mapped to HA alarm states.

| Panel State           | Proto | HA State               | Arm Action     |
|-----------------------|-------|------------------------|----------------|
| Disarmed              | D     | DISARMED               | disarm         |
| Partial + Perimeter   | B     | ARMED_HOME             | arm_home       |
| Total + Perimeter     | A     | ARMED_AWAY             | arm_away       |
| Perimeter only        | E     | ARMED_CUSTOM_BYPASS    | display only   |
| Partial (no peri)     | P     | ARMED_CUSTOM_BYPASS    | display only   |
| Total (no peri)       | T     | ARMED_CUSTOM_BYPASS    | display only   |

Three actionable states (disarmed, arm_home, arm_away). Three display-only
states mapped to ARMED_CUSTOM_BYPASS for non-standard combinations that can
occur via keypad or Verisure app.

Transitional states: ARMING while arm poll is in progress, DISARMING while
disarm poll is in progress.

Supported features flag: `ARM_HOME | ARM_AWAY`. No ARM_NIGHT, no
ARM_CUSTOM_BYPASS action, no TRIGGER.

## Config Flow

### Step 1: Credentials (`async_step_user`)

Form: username, password.

- Instantiate `VerisureClient` with generated `device_id` and `uuid`
- Call `client.login()`
- On `AuthenticationError`: show error, retry form
- On `TwoFactorRequiredError`: store OTP hash and phone list, go to step 2
- On success: go to step 3

### Step 2: Two-Factor Auth (`async_step_2fa`)

Form: phone selection (if multiple), SMS code.

- Call `client.send_otp(phone_id, otp_hash)` on phone selection
- User enters SMS code
- Call `client.validate_device(otp_hash, sms_code)`
- Verisure IT returns `hash=null` on validate success — call `client.login()` again
- On success: go to step 3
- On failure: show error, retry

### Step 3: Installation (`async_step_installation`)

- Call `client.list_installations()`
- If single installation: auto-select
- If multiple: show selection form
- Call `client.get_services(installation)` to verify access
- Create config entry

### Persisted Data (`config_entry.data`)

```python
{
    "username": str,
    "password": str,
    "device_id": str,      # persist across restarts, no re-2FA
    "uuid": str,            # persist across restarts, no re-2FA
    "installation_number": str,
    "installation_panel": str,
    "installation_alias": str,
}
```

### Options Flow

- `poll_interval`: int (seconds, default 5)

## Coordinator

Subclass of `DataUpdateCoordinator[VerisureStatusData]`.

Owns the `VerisureClient` instance and the `aiohttp.ClientSession`.

### Data Model

```python
@dataclass(frozen=True)
class VerisureStatusData:
    alarm_state: AlarmState
    proto_code: ProtoCode
    timestamp: str
    exceptions: list[dict[str, str]]  # from xSStatus, for future trigger detection
```

### Update Method (`_async_update_data`)

1. Call `client.get_general_status(installation)` — passive xSStatus
2. Parse `status` field via `parse_proto_code` into `AlarmState`
3. Capture `exceptions` field (parsed but not acted on until trigger
   detection is E2E validated)
4. Return `VerisureStatusData`

### Error Handling

| Exception              | Action                                         |
|------------------------|-------------------------------------------------|
| `SessionExpiredError`  | Re-login automatically, retry                   |
| `AuthenticationError`  | Raise `ConfigEntryAuthFailed` (HA reauth flow)  |
| `APIConnectionError`   | Raise `UpdateFailed` (HA retries with backoff)   |
| `WAFBlockedError`      | Raise `UpdateFailed` (HA retries with backoff)   |
| `UnexpectedStateError` | Log error, raise `UpdateFailed`                  |

### Lifecycle

- Created in `async_setup_entry`
- `aiohttp.ClientSession` created and owned by coordinator
- Login performed during first refresh
- Session closed in `async_unload_entry`

## Alarm Control Panel Entity

Extends `CoordinatorEntity[VerisureCoordinator]` and
`AlarmControlPanelEntity`.

### Properties

- `alarm_state`: maps coordinator data to `AlarmControlPanelState` enum
- `supported_features`: `ARM_HOME | ARM_AWAY`
- `code_arm_required`: `False` (API authentication is sufficient)
- `extra_state_attributes`: includes `force_arm_available` (bool) and
  `arm_exceptions` (list of zone aliases) when a force-arm context exists

### Actions

**`async_alarm_arm_home(code=None)`**
- Set state to ARMING
- Call `client.arm(installation, PARTIAL_PERIMETER)`
- On success: trigger coordinator refresh
- On `ArmingExceptionError`: enter force-arm flow (see below)

**`async_alarm_arm_away(code=None)`**
- Set state to ARMING
- Call `client.arm(installation, TOTAL_PERIMETER)`
- On success: trigger coordinator refresh
- On `ArmingExceptionError`: enter force-arm flow (see below)

**`async_alarm_disarm(code=None)`**
- Set state to DISARMING
- Call `client.disarm(installation)`
- On success: trigger coordinator refresh

### Force-Arm Flow

When arm hits open zones (NON_BLOCKING error with allowForcing):

1. `client.arm()` detects `res: ERROR`, `error.type: NON_BLOCKING`,
   `error.allowForcing: true` in arm status poll
2. Client calls `xSGetExceptions(referenceId, suid)` to fetch zone details:
   `[{status, deviceType, alias}]`
3. Client raises `ArmingExceptionError(reference_id, suid, exceptions)`
4. Entity catches it:
   a. Stores force context: `{reference_id, suid, mode, exceptions, created_at}`
   b. Sets `force_arm_available: true` in entity attributes
   c. Sets `arm_exceptions: ["finestracucina", ...]` in entity attributes
   d. Creates persistent notification: "Arming blocked: finestracucina is open"
   e. Fires event `verisure_it_arming_exception`:
      ```json
      {
        "entity_id": "alarm_control_panel.verisure_it",
        "zones": ["finestracucina"],
        "mode": "armed_away"
      }
      ```
   f. Reverts entity state to previous state (NOT arming — arm failed)

### `verisure_it.force_arm` Service

- Input: `entity_id`
- Reads stored force context
- Calls `client.arm(installation, target_state, force_arming_remote_id=reference_id)`
- Clears force context on success
- Dismisses persistent notification
- Triggers coordinator refresh
- Raises `ServiceValidationError` if no force context available

### Force Context Expiration

Force context is cleared:
- On successful force arm
- On explicit cancel (`verisure_it.force_arm_cancel` service)
- On next coordinator refresh (stale context = gone)

### Automation Examples

**Interactive (home) — actionable notification:**
```yaml
trigger:
  - platform: event
    event_type: verisure_it_arming_exception
action:
  - service: persistent_notification.create
    data:
      title: "Open zones detected"
      message: "{{ trigger.event.data.zones | join(', ') }} — force arm?"
  # User calls verisure_it.force_arm from dashboard or notification
```

**Automatic (away / midnight) — force arm + notify:**
```yaml
trigger:
  - platform: event
    event_type: verisure_it_arming_exception
action:
  - service: verisure_it.force_arm
    target:
      entity_id: "{{ trigger.event.data.entity_id }}"
  - service: notify.mobile_app
    data:
      title: "Alarm armed with open zones"
      message: "{{ trigger.event.data.zones | join(', ') }}"
      data:
        priority: high
```

## API Client Changes Required

### New Exception

```python
class ArmingExceptionError(VerisureError):
    """Arming blocked by open zones. Carries force-arm context."""
    reference_id: str
    suid: str
    exceptions: list[ZoneException]  # [{status, device_type, alias}]
```

### New Model

```python
class ZoneException(BaseModel):
    """An open zone reported during arming."""
    status: str
    device_type: str = Field(alias="deviceType")
    alias: str
```

### Model Changes

- `PanelError`: add `suid: str | None = None` field
- `GeneralStatus`: add `exceptions: list[ZoneException] | None = None`

### GraphQL Changes

- Add `GET_EXCEPTIONS_QUERY` — `xSGetExceptions` with referenceId, suid, counter
- `ARM_STATUS_QUERY`: add `$forceArmingRemoteId: String` variable
- `ARM_PANEL_MUTATION`: add `$forceArmingRemoteId: String` variable

### Client Method Changes

- `arm()`: accept optional `force_arming_remote_id: str | None` parameter.
  On NON_BLOCKING error with allowForcing, call `_get_exceptions()` and
  raise `ArmingExceptionError`
- New `_get_exceptions()`: polls `xSGetExceptions` until OK or timeout,
  returns list of zone exceptions
- `get_general_status()`: parse and return exceptions field

## Future Work (post-E2E validation)

### Trigger Detection

Blocked on E2E test (trip alarm with poller running, observe API response).

- Parse `exceptions` from xSStatus during triggered state
- Add TIMELINE query (service id=506) for event history
- If API reports triggered state: map to HA `TRIGGERED` state
- Fire `verisure_it_alarm_triggered` event for critical notifications

### Open Questions

- Does xSStatus report triggered alarm state? (test by tripping alarm)
- Does partial+perimeter reject on open zones? (E2E showed it armed
  silently — only total might reject)
- What does the `exceptions` field in xSStatus contain during normal
  operation vs triggered state?
