---
status: active
---

# Todo

Backlog for ha-verisure. Prune aggressively — completed items go in
`CHANGELOG.md`, not here. Keep context on pending items so the next
session can pick them up cold.

Updated: 2026-04-20.

## Immediate

- **CENT panel command map (issue #1)** — blocked on reporter's probe.
  When it arrives: inspect `services[*].attributes` for command strings;
  if absent (likely, per SDVECU finding), move to mitmproxy capture on
  the Android Verisure app. Add `PANEL_COMMAND_MAPS[CENT]`, extend
  `SUPPORTED_PANELS`. Reference: `docs/findings/panel-types.md`,
  `docs/findings/panel-SDVECU-probe.json` (SDVECU has `ARM.attributes: []`).

## High

- **Alarm trigger detection** — `xSStatus` fetches
  `exceptions { status deviceType alias }` but the `GeneralStatus`
  model drops the field. Capture a live trigger, then surface the
  data as an HA event. See
  [`findings/verisure-api.md`](findings/verisure-api.md).

## Medium

- **TIMELINE service (id 506)** — operation exists on the panel but
  GraphQL query name is unknown. Capture via webapp DevTools (see
  [`findings/cameras.md`](findings/cameras.md)).
- **Higher-resolution camera images** — all `xSRequestImages` output
  is 640×352 LOW. Worth revisiting if Verisure exposes a different
  endpoint.

## Observation

- **HA 2026.4+ thread safety** — monitor for new `async_call_later`
  callbacks that touch state directly.
