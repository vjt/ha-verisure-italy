# Panel Types

This integration only sends arm/disarm commands to panel types it has
been explicitly verified against. The `SUPPORTED_PANELS` allowlist
(`verisure_italy/models.py`) is the single source of truth. Unknown
panels raise `UnsupportedPanelError` on arm/disarm — no command is sent.

## Verified

| Panel    | Notes |
|----------|-------|
| `SDVECU` | Developer's own panel. Command map in `STATE_TO_COMMAND` (DARM1, ARM1, ARM1PERI1, ARMDAY1PERI1, DARM1DARMPERI). Reference probe at [`panel-SDVECU-probe.json`](panel-SDVECU-probe.json). |

## Adding a New Panel

We won't guess commands. The workflow is:

1. **Reporter runs the probe** (either the CLI or upgrades and reads
   the HA log — see issue template).
2. **Reporter attaches the probe JSON** to a GitHub issue.
3. We inspect the probe's `services` (especially any `attributes`
   arrays) and the full `devices` list to figure out what subset of
   services the panel supports and whether the API declares the exact
   request strings for arm/disarm.
4. If attributes don't carry the strings (they don't on SDVECU — the
   ARM service has `attributes: []`), the next step is a mobile-app
   HTTP capture (mitmproxy on the Android device → hit Arm/Disarm →
   read the `request` value from the real mutation). That gives the
   exact strings to commit as `PANEL_COMMAND_MAPS[PANEL]`.
5. Patch: add command map, add panel to `SUPPORTED_PANELS`, commit a
   redacted probe as `panel-<NAME>-probe.json`.

## Probe Contents (schema v1)

The probe is strictly read-only:

- `xSInstallations`  (already parsed by client; panel code comes from here)
- `xSSrv`            (services + attributes + capabilities JWT)
- `xSDeviceList`     (raw device dump)
- `xSStatus`         (server-cached alarm status — no panel ping)

No arm/disarm, no `xSCheckAlarm` (that one pings the physical panel
and writes to the timeline — we avoid it here).

## Redaction

The probe drops all PII before writing to the log or stdout:

- `numinst` → 8-char sha256 prefix (`numinst_hash`). In the committed
  reference fixture even that hash is replaced with `"REDACTED"` —
  a 7-digit numeric input is trivially brute-forceable.
- Names, addresses, phone, email, device aliases, serial numbers,
  JWT tokens, reference IDs — all dropped.
- Kept: panel code, installation type, all service fields (including
  attributes), device types/codes/zones, alarm proto codes.

The probe module exports `assert_redacted()` which walks the output
and refuses to emit anything containing a PII-named field. A unit
test asserts every sensitive value is absent on synthetic input.

## SDVECU Reference Observations

From the committed reference probe:

- Services declared: 41 total, with 7 active+visible common ones (IMG,
  EST, ARM, DARM, CAMERAS, ARMNIGHT, TIMELINE, DEACTIVATEZONE,
  CONNSTATUS).
- Services carrying `attributes`: only non-alarm ones (BILLS, ESTINV,
  SCH, WHATSAPP). The **ARM and DARM services have `attributes: []`**
  — so the exact mutation strings `ARM1`, `ARM1PERI1`, `DARM1DARMPERI`
  etc. are **not** discoverable via `xSSrv` alone.
- Device types present: `MG` (magnetic sensors), `VV` (volumetric),
  `FX` (shock), `QR`/`QP` (cameras), `VK` (keypad). No `CENT` entry
  (which is what the issue #1 reporter's panel type-code is — it
  likely appears inside their probe, not ours).
- No `DOORLOCK`, no sentinel/CONFORT sensors.

Bottom line: for SDVECU the command map was found by observing the
mobile app's HTTP traffic, not by reading `Service.attributes`.
Expect the same for CENT.
