# Panel Types

This integration only sends arm/disarm commands to panel types it has
been explicitly verified against. The `SUPPORTED_PANELS` allowlist
(`verisure_italy/models.py`) is the single source of truth. Unknown
panels raise `UnsupportedPanelError` on arm/disarm — no command is sent.

## Verified

| Panel    | Notes |
|----------|-------|
| `SDVECU` | Developer's own panel. Commands resolved via `CommandResolver` + active-service gating (DARM1, ARM1, ARM1PERI1, ARMDAY1PERI1, DARM1DARMPERI). Reference probe at [`panel-SDVECU-probe.json`](panel-SDVECU-probe.json). |

## Adding a New Panel

**Superseded 2026-04-24.** The web-bundle dissection (see
[`arm-command-vocabulary.md`](arm-command-vocabulary.md)) enumerates
all 8 API-recognised panel types and the full enum of valid
arm/disarm wire values directly from `customers.verisure.it`'s JS
bundle. The reporter-side mitmproxy step is no longer needed for
string discovery.

Current workflow:

1. **Reporter attaches probe JSON** (CLI or HA log) to a GitHub issue.
2. We read `installation.panel` and cross-reference the panel roster
   in [`arm-command-vocabulary.md`](arm-command-vocabulary.md) to
   classify the family (peri-capable vs not).
3. We read `services[].active` to determine which enum members the
   panel accepts (e.g. SDVFAST has `PERI.active=false` → perimeter
   variants rejected).
4. Patch: add panel to `SUPPORTED_PANELS`, ensure the command
   resolver branches correctly for that panel's active-service set,
   commit a redacted probe as `panel-<NAME>-probe.json`.
5. **First live confirmation** still required before claiming
   support: reporter performs one disarm → we match the resulting
   proto response code against `PROTO_TO_STATE`. Unknown codes crash
   loud (fail-secure).

Re-run [`scripts/dissect-web-bundle.sh`](../../scripts/dissect-web-bundle.sh)
before each release to catch upstream drift (new panel codes, new
enum members, resolver changes).

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
