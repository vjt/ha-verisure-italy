---
status: active
---

# Todo

Backlog for ha-verisure. Prune aggressively — completed items go in
`CHANGELOG.md`, not here. Keep context on pending items so the next
session can pick them up cold.

Updated: 2026-04-24 (post-v0.9.2 INTERIOR_ONLY state-mapping fix).

## Immediate

- **Issue #1 arm failure (ewinters-ca-spark)** — v0.9.0 nudge posted
  2026-04-24. Reporter's `xSDeviceList` had a device with `type: CENT`
  ("Pannello di Controllo"), which earlier notes mistakenly tracked as
  the panel model — `CENT` is the device type for the control-panel
  unit, not `installation.panel`. Reporter's actual panel model is
  unknown; likely one of the 8 now supported in v0.9.0. When they
  respond: if arm works, close the issue. If a `VERISURE ARM FAILURE
  BEGIN` block lands, use it to identify the panel + any missing
  service gate. Reference:
  `docs/findings/arm-command-vocabulary.md`.
- **Issue #3 SDVFAST live confirmation** — alan210874 confirmed v0.9.1
  broken on SDVFAST (`UnsupportedCommandError: ARMDAY1PERI1 missing
  PERI`). Root cause was entity-layer target assumption; fixed in
  v0.9.2 by panel-family-aware arm targets (see CP04 S2). v0.9.2
  comment posted 2026-04-24 asking reporter to update via HACS and
  retest arm_home + arm_away. On success: close the issue. On a new
  `VERISURE ARM FAILURE BEGIN` block: use it to identify any
  remaining SDVFAST-specific quirk.

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
- **M7 — client → HA model-leak boundary** (deferred from 2026-04-24
  codebase review). Some Pydantic models from `verisure_italy.models`
  are passed into HA entity state attributes / extra_state_attributes
  unwrapped (`ZoneException` in the force-context attributes is the
  clearest case). Long-term we want a dedicated "integration-facing"
  view type that the entity layer constructs from the client model.
  Needs a design pass — the current leak is harmless but makes the
  boundary harder to enforce on future changes. See
  `docs/reviews/2026-04-24-codebase-review.md` section M7.

## Observation

- **HA 2026.4+ thread safety** — monitor for new `async_call_later`
  callbacks that touch state directly.
