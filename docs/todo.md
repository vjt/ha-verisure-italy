---
status: active
---

# Todo

Backlog for ha-verisure. Prune aggressively — completed items go in
`CHANGELOG.md`, not here. Keep context on pending items so the next
session can pick them up cold.

Updated: 2026-04-24.

## Immediate

- **CENT panel (issue #1)** — blocked on reporter's probe.
  When it arrives: inspect `installation.panel` to classify family
  (peri-capable vs interior-only) and read `services[].active` to
  determine capability set. Add panel to `SUPPORTED_PANELS` +
  `PANEL_FAMILIES`; `CommandResolver` handles command routing via the
  active-service capability filter. Reference: `docs/findings/panel-types.md`,
  `docs/findings/arm-command-vocabulary.md`.

## High

- **Alarm trigger detection + HA notification when the alarm rings** —
  need to push an HA event the moment the panel goes into alarm state
  (faster than the 15s `xSStatus` poll). Two possible signal sources:
  `xSStatus.exceptions { status deviceType alias }` (already fetched,
  currently dropped by the `GeneralStatus` model) and `xSActV2` timeline
  signal types in the 5xx/7xx range (see below). Capture a live trigger
  first, then surface as an HA event + notification. See
  [`findings/verisure-api.md`](findings/verisure-api.md) and
  [`findings/timeline-api.md`](findings/timeline-api.md).
- **Alarm report browsing** — the web UI `/owa-static/timeline` surfaces
  "VIEW REPORT" buttons on past alarms. We want to (a) fetch past alarm
  reports programmatically, (b) expose them as an HA sensor or attribute
  for automations. Likely piggy-backs on `xSActV2` (already reverse-
  engineered — see [`findings/timeline-api.md`](findings/timeline-api.md))
  plus a per-incidence detail endpoint. Dissect the web bundle for the
  detail query shape.

## Medium

- **TIMELINE / `xSActV2` integration** — query shape + response captured
  in [`findings/timeline-api.md`](findings/timeline-api.md). Surface as
  a read-only HA logbook/sensor for recent activity. Separate PR from
  trigger detection above.
- **Higher-resolution camera images** — all `xSRequestImages` output
  is 640×352 LOW. Worth revisiting if Verisure exposes a different
  endpoint.

## Observation

- **HA 2026.4+ thread safety** — monitor for new `async_call_later`
  callbacks that touch state directly.
