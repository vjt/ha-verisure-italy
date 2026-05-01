---
status: active
---

# Todo

Backlog for ha-verisure. Prune aggressively — completed items go in
`CHANGELOG.md`, not here. Keep context on pending items so the next
session can pick them up cold.

Updated: 2026-05-01 (v0.9.4 shipped for Issue #5; #5 + #6 closed by
reporter; Issue #1 re-pinged with v0.9.4 ask).

## Immediate

- **Issue #1 arm failure (ewinters-ca-spark)** — re-pinged 2026-05-01
  with v0.9.4 update: panel-type discovery now has a real allowlist
  (`SUPPORTED_PANELS`) with self-contained probe block on unknown
  panels, arm-command resolution mirrors the official web-app gate
  (per-user partition permissions via `configRepoUser.alarmPartitions`),
  failure reports include partition snapshot + command + family.
  Five releases (v0.9.0 → v0.9.4) silent since the original nudge.
  The reporter's `xSDeviceList` had a device with `type: CENT`
  ("Pannello di Controllo") — `CENT` is the device type for the
  control-panel unit, not `installation.panel`. Actual panel model
  unknown; likely one of the 8 supported panels. When they respond:
  if arm works, close. If a `VERISURE ARM FAILURE BEGIN` block
  lands, the new partition + command fields should pinpoint cause
  at a glance. Reference: `docs/findings/arm-command-vocabulary.md`.
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

- **Fold dist-info prune into `verisure-deploy` skill.** Hot-deploying
  `verisure_italy/*.py` into the HA container leaves stale
  `*.dist-info` dirs behind. `importlib.metadata.version()` reads
  metadata, not source, and resolves to the lexicographically-first
  dist-info — so HA's `manifest.json` requirements check fails after
  every version bump until the old dist-infos are removed. Hit
  during v0.9.3 (CP05 S2) and AGAIN during v0.9.4 deploy
  (2026-05-01) — the same workaround (rename dist-info dir + patch
  `METADATA: Version:`) had to be repeated by hand. Cleanest fix:
  after the client-lib deploy step, the skill should `rm -rf` any
  `verisure_italy-*.dist-info` whose version doesn't match the
  freshly-deployed `verisure_italy/__init__.py:__version__`, then
  rename the survivor (or patch its `METADATA: Version:` in place).
