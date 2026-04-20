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
- [`panel-SDVECU-probe.json`](panel-SDVECU-probe.json) — redacted
  reference probe for the verified SDVECU panel
