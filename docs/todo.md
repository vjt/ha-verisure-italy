---
status: active
---

# Todo

Backlog for ha-verisure. Prune aggressively — completed items go in
`CHANGELOG.md`, not here. Keep context on pending items so the next
session can pick them up cold.

Updated: 2026-04-30 (v0.9.3 shipped for Issue #4 — laurafabry
SDVECU-no-EST. Awaiting reporter confirmation; Issue #1 still
silent since v0.9.0).

## Immediate

- **Issue #4 confirmation pending (laurafabry)** — v0.9.3 shipped
  2026-04-30 with service-aware `effective_family()` — SDVECU
  installs lacking `EST` in `xSSrv` now demote to `INTERIOR_ONLY`
  and arm via `ARMDAY1` / `ARM1` (matches Verisure IT web app on
  the same hardware). Reporter pinged on the issue with upgrade
  instructions in Italian + full English root-cause. Close on
  confirmation; if a new `VERISURE ARM FAILURE BEGIN` lands, treat
  as a new vector. Reference: `docs/findings/arm-command-vocabulary.md`.

- **Issue #1 arm failure (ewinters-ca-spark)** — no reply since the
  2026-04-24 v0.9.0 nudge; v0.9.1 + v0.9.2 + v0.9.3 have shipped
  since. The reporter's `xSDeviceList` had a device with `type:
  CENT` ("Pannello di Controllo"), which earlier notes mistakenly
  tracked as the panel model — `CENT` is the device type for the
  control-panel unit, not `installation.panel`. Actual panel model
  unknown; likely one of the 8 supported panels. When they respond:
  if arm works, close. If a `VERISURE ARM FAILURE BEGIN` block
  lands, use it to identify the panel + any missing service gate.
  Reference: `docs/findings/arm-command-vocabulary.md`.
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
  during the v0.9.3 deploy (CP05 S2). Cleanest fix: after the client-
  lib deploy step, the skill should `rm -rf` any
  `verisure_italy-*.dist-info` whose version doesn't match the
  freshly-deployed `verisure_italy/__init__.py:__version__`, then
  clone the canonical dist-info from the highest-numbered remaining
  one (or just patch its `METADATA: Version:` line in place).
