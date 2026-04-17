# Verisure IT API — E2E Findings

Live behavior discovered during E2E testing (2026-04-02+) against the
Italian Verisure API. These are IT-specific deviations from the
upstream `securitas-direct-new-api` assumptions.

## Auth flow

- `validate_device` returns `hash: null` on success — device is
  authorized but no token. Caller must `login()` again.
- `changePassword: true` blocks login with null hash. Change the
  password via the Verisure mobile app first.
- 2FA is per `(device_id, uuid)` pair. New `uuid` = new 2FA required.
- Device credentials (`device_id`, `uuid`) must persist across restarts.

## API field quirks

- Many `msg` fields are `null`, not empty string (OTP result, validate
  result, poll WAIT responses).
- Installation field is `numinst`, not `number`.
- Service descriptions are all `null`.
- Panel executes commands fire-and-forget — even if our code crashes
  during poll parsing, the panel still acts.

## Disarm without permission

- Returns `res: ERROR`, `msg: alarm-manager.error_no_response_to_request`.
- Arrives through the normal poll response, not as HTTP or GraphQL error.

## Open zone behavior (confirmed 2026-04-02)

- Arming `PARTIAL+PERIMETER` with an open window: panel arms silently,
  `error: null`, no warning. Perimeter sensor then TRIGGERS because the
  window is open — alarm goes off.
- Only `TOTAL` mode appears to reject with `NON_BLOCKING` error
  (unconfirmed).

## Arm exception / force-arm flow

See [`arm-exception-flow.md`](arm-exception-flow.md) for the full
capture.

Summary:
- Arm status poll returns `res: ERROR`, `error.type: "NON_BLOCKING"`,
  `error.allowForcing: true`, plus `referenceId` + `suid`.
- Client calls `xSGetExceptions(referenceId, suid)` → returns
  `[{status, deviceType, alias}]`.
- Force arm: re-send `xSArmPanel` with both `forceArmingRemoteId` (the
  error's `referenceId`) AND `suid`. Missing `suid` may cause the panel
  to reject the force.

## Alarm trigger detection (open)

- `xSStatus` already fetches `exceptions { status deviceType alias }`
  but the `GeneralStatus` model currently drops the data.
- TIMELINE service (id=506) exists on the panel — query name not yet
  reverse-engineered. See [`cameras.md`](cameras.md) for the discovery
  approach.
- Goal: detect alarm trigger before Verisure CRA triages it → HA
  notification.
