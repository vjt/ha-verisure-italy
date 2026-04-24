# Timeline API (`xSActV2`) — Reverse-Engineered

The Verisure IT web app `customers.verisure.it/owa-static/timeline`
fetches the alarm/activity timeline via the `ActV2Timeline` GraphQL
query. This is the backing endpoint for the `TIMELINE` service
(`idService=506`) in `xSSrv`.

Captured 2026-04-24 from a live session with the web portal.

## GraphQL operation

Operation name: `ActV2Timeline`
Root field: `xSActV2`

```graphql
query ActV2Timeline(
  $numinst: String!
  $offset: Int
  $hasLocksmithRequested: Boolean
  $singleActivityFilter: [Int]
  $signalsToExclude: [Int]
  $timeFilter: TimeFilter!
  $numRows: Int
  $dateTo: Datetime
  $dateFrom: Datetime
  $idDevice: String
  $alias: String
  $panel: String
  $lix: String
) {
  xSActV2(
    numinst: $numinst
    input: {
      timeFilter: $timeFilter
      numRows: $numRows
      offset: $offset
      dateFrom: $dateFrom
      dateTo: $dateTo
      singleActivityFilter: $singleActivityFilter
      signalsToExclude: $signalsToExclude
      hasLocksmithRequested: $hasLocksmithRequested
      idDevice: $idDevice
      alias: $alias
      panel: $panel
      lix: $lix
    }
  ) {
    reg {
      alias
      type
      device
      source
      idSignal
      schedulerType
      myVerisureUser
      time
      img
      incidenceId
      signalType
      interface
      deviceName
      keyname
      tagId
      userAuth
      exceptions {
        status
        deviceType
        alias
      }
      mediaPlatform {
        serialNumber
        mediaId
      }
    }
  }
}
```

## Request variables (example, as sent by the web on page load)

```json
{
  "numinst": "<installation number>",
  "offset": 0,
  "numRows": 30,
  "hasLocksmithRequested": false,
  "singleActivityFilter": [0],
  "timeFilter": "LASTMONTH",
  "dateFrom": "",
  "panel": "SDVECU",
  "signalsToExclude": [14, 17, 24, 241, 502, 506, 508, 531, 537, 538,
    539, 540, 541, 542, 543, 544, 546, 547, 581, 582, 583, 590, 591,
    5800, 5801, 5802, 5820, 5821, 5822, 5823, 5824,
    325, 323, 324, 326, 327, 328, 329,
    315, 316, 317, 318, 319, 320, 321, 322, 330]
}
```

- `singleActivityFilter: [0]` in observed calls — "0" appears to be
  "no single-activity filter" (returns all). Other values would filter
  by a specific signal category.
- `signalsToExclude` is a WAF/UI filter — the web app excludes a
  curated set of noise signals (remote commands, error codes, etc.)
  before rendering. For a comprehensive integration we'd likely send
  `signalsToExclude: []` instead.

## Response shape

`data.xSActV2.reg` is a list of `XSActV2Reg` records, newest first:

```json
{
  "alias": "Connection Exterior + Main total",
  "type": 823,
  "device": null,
  "source": "Android",
  "idSignal": "798337691",
  "schedulerType": null,
  "myVerisureUser": "Home Assistant",
  "time": "2026-04-23 16:01:25",
  "img": 0,
  "incidenceId": null,
  "signalType": 823,
  "interface": null,
  "deviceName": null,
  "keyname": null,
  "tagId": null,
  "userAuth": null,
  "exceptions": null,
  "mediaPlatform": null
}
```

## Observed signal types (non-exhaustive)

From live captures on SDVECU:

| `type` / `signalType` | Meaning | Source |
|-----------------------|---------|--------|
| `822` | Disconnection Exterior + Main (disarm all via app) | `"Android"` / `"iOS"` |
| `823` | Connection Exterior + Main total (arm total + peri via app) | `"Android"` / `"iOS"` |
| `824` | Connection Exterior + Main partial (arm partial + peri via app) | `"Android"` / `"iOS"` |
| `720` | Disarmed via keypad (with device + interface) | `"02"` = keypad interface |
| `700` | Disarmed — companion signal to 720 (interior vs perimeter side) | `"02"` |

The full signal-type enum is not captured here yet — it's likely the
`Signals` table referenced by `xSGetSignals` / `mkGetSrvSignals`.
Extend when we need more coverage.

**Important**: signal types encode *resulting state*, not the mutation
string sent. `request` field is absent. This is consistent with the
timeline being an audit view, not a command log — the command layer
is the `xSArmPanel` / `xSDisarmPanel` response.

## Source field

- `"Android"` / `"iOS"` — mobile app (the "Android" source shows
  "By Home Assistant From Android" because our HA client presents
  itself as `samsung SM-S901U`, Android 12 — see `client.py` device
  constants).
- `null` with `interface: "02"` — physical keypad.
- `null` with `interface` other codes — other on-premises controls.

## Future use

- **Dashboard card**: last N timeline events as a HA list/logbook
  entity. Read-only, zero panel interaction.
- **Alarm-trigger detection**: filter for alarm-family signal types
  (likely in the `5xx` / `7xx` ranges) → push HA event earlier than
  the 15-second `xSStatus` poll.
- **Arm audit / diagnostic**: correlate our `xSArmPanel` reference ids
  with resulting timeline entries — would catch silent arm failures.

None of the above requires changing how commands are sent; it's
pure observation.
