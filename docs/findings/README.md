# Findings

Reverse-engineered protocol behavior, API quirks, and tuning results
for the Verisure IT cloud API. These are captures from live systems —
timestamps on each file indicate when the observation was made.

The Verisure API is undocumented. Everything here was discovered by:
- Live E2E testing against the customer's own panel
- Capturing webapp traffic via browser DevTools
- Diffing behavior against the upstream
  `securitas-direct-new-api` project

## Contents

- [`verisure-api.md`](verisure-api.md) — auth flow, field quirks,
  open-zone behavior, trigger detection TODO
- [`arm-exception-flow.md`](arm-exception-flow.md) — full arm →
  exception → force-arm capture
- [`cameras.md`](cameras.md) — hardware limits, active vs passive
  capture, undiscovered APIs
- [`camera-capture-tuning.md`](camera-capture-tuning.md) — 2 s
  stagger sweet spot for parallel captures
- [`panel-types.md`](panel-types.md) — supported panel types and the
  workflow for adding a new one
- [`arm-command-vocabulary.md`](arm-command-vocabulary.md) — complete
  `ArmCodeRequest` / `DisarmCodeRequest` enum + decoded target→command
  resolver + panel roster (8 panels, two families) extracted from the
  web bundle
- [`timeline-api.md`](timeline-api.md) — `xSActV2` / `ActV2Timeline`
  query shape + response, signal type codes observed, basis for a
  future dashboard card
- [`panel-SDVECU-probe.json`](panel-SDVECU-probe.json) — redacted
  reference probe for the verified SDVECU panel
- [`unavailable-flapping.md`](unavailable-flapping.md) — why the
  alarm entity flaps to `unavailable` and the 6h sticky outage root
  cause (over-broad `AuthenticationError` classification)

Automation: [`scripts/dissect-web-bundle.sh`](../../scripts/dissect-web-bundle.sh)
auto-detects the latest web bundle, downloads it, and regenerates the
enum / resolver / panel-roster sections above.
