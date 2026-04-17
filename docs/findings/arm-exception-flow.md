# Arm → Exception → Force-arm → Disarm Flow

Captured from the Verisure webapp on 2026-04-03.

## Role gotcha

RESTRICTED role users do **not** get zone exceptions — the panel arms
regardless and sensors trip on the open zone. ADMIN role is required
for the force-arm flow to fire. Use an admin-scoped API user.

## Flow

1. `xSArmPanel` → `res: OK`, `referenceId`.
2. `ArmStatus` poll (counter 1) → `res: WAIT`.
3. `ArmStatus` poll (counter 2) → `res: ERROR`,
   `msg: error_mpj_exception`,
   `error.type: NON_BLOCKING`,
   `error.allowForcing: true`,
   `error.code: "102"`,
   `error.suid: "1234567VI5XMxnbcA=="`.
4. `xSGetExceptions(referenceId, suid)` → exceptions array, e.g.
   `{status: "0", deviceType: "MG", alias: "Finstudio1"}`.
5. `xSArmPanel` again with BOTH `forceArmingRemoteId` AND `suid` →
   new `referenceId`.
6. `ArmStatus` polls until `res: OK`, `protomResponse: "B"` (armed
   partial + perimeter).
7. `xSStatus` now surfaces the bypassed zones in `exceptions`.

## Why `suid` matters

The `suid` identifies the specific exception session. The webapp
always sends both `forceArmingRemoteId` AND `suid` on the force retry
— omitting `suid` risks the panel rejecting the force-arm.
