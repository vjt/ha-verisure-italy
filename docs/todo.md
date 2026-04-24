---
status: active
---

# Todo

Backlog for ha-verisure. Prune aggressively — completed items go in
`CHANGELOG.md`, not here. Keep context on pending items so the next
session can pick them up cold.

Updated: 2026-04-24 (post-v0.9.2 + todo cleanup: merged xSActV2 items,
reworded Issue #1 and #3, dropped HA 2026.4 monitoring).

## Immediate

- **Issue #1 arm failure (ewinters-ca-spark)** — no reply since the
  2026-04-24 v0.9.0 nudge; v0.9.1 + v0.9.2 have shipped since. The
  reporter's `xSDeviceList` had a device with `type: CENT` ("Pannello
  di Controllo"), which earlier notes mistakenly tracked as the panel
  model — `CENT` is the device type for the control-panel unit, not
  `installation.panel`. Actual panel model unknown; likely one of the
  8 supported panels. When they respond: if arm works, close. If a
  `VERISURE ARM FAILURE BEGIN` block lands, use it to identify the
  panel + any missing service gate. Reference:
  `docs/findings/arm-command-vocabulary.md`.
- **Issue #3 v0.9.2 retest confirmation (alan210874, SDVFAST)** —
  v0.9.1 surfaced `UnsupportedCommandError: ARMDAY1PERI1 missing
  PERI` on their panel; root cause was the entity-layer hard-coded
  perimeter target, fixed in v0.9.2 by panel-family-aware arm targets
  (see CP04 S2). v0.9.2 comment posted 2026-04-24 asking for a
  HACS update + arm_home/arm_away retest. On success: close. On a
  new `VERISURE ARM FAILURE BEGIN` block: identify any remaining
  SDVFAST-specific quirk.

## High

- **xSActV2 observability** — the alarm timeline query is reverse-
  engineered (`docs/findings/timeline-api.md`) and underpins three
  related but independently-shippable deliverables. Each is its own
  PR:
  - **Alarm trigger detection + HA event** — push a fast HA event
    the moment the panel rings (faster than the 15s `xSStatus`
    poll). Signal sources: `xSStatus.exceptions { status deviceType
    alias }` (already fetched, dropped by `GeneralStatus`) and
    `xSActV2` signal types in the 5xx / 7xx range. Needs a live
    trigger capture first. See `docs/findings/verisure-api.md`.
  - **Alarm report browsing** — the web UI's "VIEW REPORT" buttons
    on `/owa-static/timeline` hit a per-incidence detail endpoint
    not yet captured. Fetch the detail query shape from the web
    bundle, expose as an HA sensor/attribute for automations.
  - **Timeline logbook** — expose recent `xSActV2` activity as a
    read-only HA logbook / sensor. Pure observation, no panel
    interaction.

## Medium

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
