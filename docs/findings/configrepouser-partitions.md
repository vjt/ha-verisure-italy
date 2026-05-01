# `configRepoUser.alarmPartitions` — Per-User Partition Permissions

Captured 2026-05-01 from a live web-app session (maintainer's
SDVECU+EST install). Source: `xSSrv` GraphQL query response —
**already on the wire**, but the `SERVICES_QUERY` shipped before
v0.9.4 didn't request the field.

## Wire shape

```graphql
xSSrv(numinst: $numinst, uuid: $uuid) {
  installation {
    configRepoUser {
      alarmPartitions {
        id
        enterStates
        leaveStates
      }
    }
  }
}
```

```json
"configRepoUser": {
  "alarmPartitions": [
    { "id": "01", "enterStates": ["01","02"], "leaveStates": ["01","02"] },
    { "id": "02", "enterStates": ["01"],      "leaveStates": ["01"] },
    { "id": "03", "enterStates": [],          "leaveStates": [] }
  ]
}
```

## Partition IDs (from web bundle constant `K`)

| ID | Meaning |
|----|---------|
| `01` | MAIN (interior) |
| `02` | PERIMETRAL |
| `03` | ANNEX |

## Gate semantics

The web-app function `z` (offset 390138 in `main.fae761fe.js`,
bundle 2.4.3) reads the partition for the relevant ID and returns
`enterStates.length > 0` (for arm) or `leaveStates.length > 0`
(for disarm). Empty arrays mean the user lacks permission —
attempting the command client-side returns `error_code 101 /
error_mpj_exception`.

## Why this is authoritative over `EST`

`EST` advertises HW provisioning at the install level. Partition
`02` permissions are per-user AND imply HW exists (you can't have
non-empty `enterStates` without underlying sensors). Two SDVECU
installs with identical `EST=active` can differ on partition `02`
permissions — laurafabry's SDVECU (Issue #5) is the proof point.

## Open questions

- The partition `enterStates` / `leaveStates` array contents
  (`"01"`, `"02"`) presumably correspond to specific protomatic
  states the user can arm to / disarm from. The current gate
  uses only non-emptiness — interpreting the values is unneeded
  for v0.9.4.
- `installation.role` (`"ADMIN"` on maintainer install) is the
  `isCU` analog used by the web bundle to flip `DARMPERI` vs
  `DARM1` for perimeter-only disarm. Out of scope until the
  integration ever needs perimeter-only disarm — currently we
  always disarm interior+perimeter together.
