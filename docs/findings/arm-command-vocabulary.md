# Arm / Disarm Command Vocabulary — Reverse-Engineered

Full `ArmCodeRequest` / `DisarmCodeRequest` enum values + per-target
resolver logic, extracted from the Verisure IT web app bundle at
`customers.verisure.it/2.4.2/static/js/main.5d2af1b6.js`
(2026-04-24 capture).

This supersedes the guessing approach in
[`panel-types.md`](panel-types.md) for vocabulary discovery. Per-panel
acceptance (which enum members a specific panel honours) is still read
from `Service.active` in `xSSrv`.

## ArmCodeRequest enum

All valid values observed in the web bundle as string literals:

| Value | Meaning |
|-------|---------|
| `ARM1` | Arm total (from disarmed) |
| `ARM1PERI1` | Arm total + perimeter (from disarmed) |
| `ARMANNEX1` | Arm annex |
| `ARMDAY1` | Arm partial/day (from disarmed) |
| `ARMDAY1PERI1` | Arm partial + perimeter (from disarmed) |
| `ARMINTEXT1` | Arm interior + exterior (Spain WAF-safe alternative for `ARM1PERI1`; unclear if accepted on IT) |
| `ARMINTFPART1` | Transition to TOTAL from any armed interior mode (DAY/PARTIAL/NIGHT) |
| `ARMNIGHT1` | Arm night (from disarmed) |
| `ARMPARTFINTDAY1` | Transition to DAY from TOTAL |
| `ARMPARTFINTNIGHT1` | Transition to NIGHT from TOTAL |
| `PERI1` | Arm perimeter only |

## DisarmCodeRequest enum

| Value | Meaning |
|-------|---------|
| `DARM1` | Disarm interior (and perimeter if not armed separately) |
| `DARM1DARMPERI` | Disarm both interior and perimeter |
| `DARMANNEX1` | Disarm annex |
| `DARMPERI` | Disarm perimeter only (installation-owner only; non-owners fall back to `DARM1`) |

## Resolver (decoded from bundle, function `w` at offset ~387880)

```text
resolve(alarmMode, currentMode, isCU):
  match alarmMode:
    ARM_TOTAL:
      if currentMode in {ARM_DAY, ARM_PARTIAL, ARM_NIGHT}: "ARMINTFPART1"
      else: "ARM1"
    ARM_INT_EXT:        "ARMINTEXT1"
    ARM_DAY:
      if currentMode == ARM_TOTAL: "ARMPARTFINTDAY1"
      else: "ARMDAY1"
    ARM_PARTIAL:        "ARMDAY1"
    ARM_NIGHT:
      if currentMode == ARM_TOTAL: "ARMPARTFINTNIGHT1"
      else: "ARMNIGHT1"
    ARM_ANNEX:          "ARMANNEX1"
    ARM_PERIMETER:      "PERI1"
    DISARM:             "DARM1"
    DISARM_PERIMETER:
      if isCU:          "DARMPERI"
      else:             "DARM1"
    DISARM_ANNEX:       "DARMANNEX1"
    DISARM_DISARM_PERIMETER:  "DARM1DARMPERI"
    ARM_PARTIAL_ARM_PERIMETER: "ARMDAY1PERI1"
    ARM_TOTAL_ARM_PERIMETER:   "ARM1PERI1"
    UNKNOWN:            "UNKNOWN"
    default:            throw "Unrecognized alarm mode"
```

`isCU` (likely "installation owner" flag) only gates `DARMPERI` vs
`DARM1` for perimeter-only disarm.

### Why this matters for our code

**Addressed in v0.9.0.** The former `models.STATE_TO_COMMAND` mapped
`AlarmState → ArmCommand` using **target state alone**. The web resolver
proves that's incomplete: switching from TOTAL to DAY without first
disarming uses `ARMPARTFINTDAY1`, not `ARMDAY1`. `ARMDAY1` applied while
currently TOTAL is probably rejected or does the wrong thing.

`STATE_TO_COMMAND` has been replaced by `CommandResolver`, which is
panel-aware, current-state-aware, and capability-gated via
`active_services()`. `client.arm()` and `client.disarm()` now call the
resolver instead of a static lookup. Multi-step transitions (TOTAL→DAY)
use the correct transition-variant strings automatically.

## Per-panel support — empirically observed

**Correction (2026-04-24)**: the original version of this table — mapping
each service flag to a specific enum subset — was **guesswork that didn't
match reality**. Live observation on an SDVECU panel (and deployment
failure) proved that `ARMDAY` and `PERI` are NOT reported as active
services on SDVECU, yet the panel happily accepts `ARMDAY1`, `ARM1PERI1`,
`ARMDAY1PERI1`, and `DARM1DARMPERI` commands.

The actual gating observed in practice:

| Signal | Meaning |
|--------|---------|
| `ARM` (active) | Panel supports **any** arm command — base gate. |
| `DARM` (active) | Panel supports **any** disarm command — base gate. |
| `ARMNIGHT` (active) | Panel supports night-mode arming (sub-cap; reliably reported). |
| `ARMANNEX` / `DARMANNEX` (active) | Panel has an annex zone (sub-cap). |
| `ARMDAY` / `PERI` flags | **UI hints, not wire-protocol gates**. Absence doesn't block the corresponding wire commands on panels that physically support them. |
| Panel FAMILY (peri-capable vs interior-only) | Model-level capability. SDVFAST/SDVFSW (interior-only family) reject every `*PERI*` command regardless of service flags — no perimeter hardware exists. |
| `EST` (active) | **Runtime perimeter-provisioning indicator**. A `PERI_CAPABLE` model can ship without perimeter sensors provisioned; in that case `EST` is absent from `xSSrv` and the panel rejects every `*PERI*` command with `code 101 / error_mpj_exception`. Drives `effective_family()` demotion (Issue #4, v0.9.3). |

### CommandResolver gating logic

1. **Base gate**: every arm variant requires `ARM`; every disarm variant requires `DARM`.
2. **Sub-capability gate**: `ARMNIGHT`/`ARMANNEX`/`DARMANNEX` required only for commands that use them (e.g. `ARMNIGHT1`).
3. **Effective-family gate**: perimeter-involving commands (`ARM1PERI1`, `ARMDAY1PERI1`, `ARMNIGHT1PERI1`, `PERI1`, `ARMINTEXT1`, `DARM1DARMPERI`, `DARMPERI`) are rejected client-side when the **effective** family is `INTERIOR_ONLY`. Effective family combines the model-level classifier (`PANEL_FAMILIES`) with a runtime demotion: a `PERI_CAPABLE` model whose `xSSrv` lacks `EST` is treated as `INTERIOR_ONLY`. The HA entity also consults effective family when picking arm targets, so the resolver never sees a perimeter target on a no-EST install in the first place; the resolver-level check is defense in depth for direct service callers.
4. **Panel rejection** remains fail-secure: if we do send a command the panel refuses, we crash loud with the full wire-level response (see failure-report markers).

`PERI` service flag is **not** used as a gate — it's observed to be absent on panels that fully support perimeter operations (maintainer's SDVECU+EST has no `PERI` in services yet accepts every `*PERI*` command). `EST` is the reliable runtime signal because it advertises sensor provisioning rather than UI capability.

## Panel roster (from bundle, 2026-04-24 / v2.4.2)

The web bundle enumerates every panel type the API supports, and
classifies them into two families via a function (here called `R(e)`,
semantically "is peri-capable"):

### Family A — peri-capable, two-axis state (interior × perimeter)

`SDVECU`, `SDVECU-D`, `SDVECU-W`, `SDVECUD`, `SDVECUW`, `MODPRO`

- The `-D` / `-W` suffix variants (and non-dashed `SDVECUD` / `SDVECUW`)
  exist in the enum as separate constants. Unclear whether the API
  accepts both forms interchangeably or only one per installation —
  treat them as distinct panel codes until observed.
- `MODPRO` is the same capability family (peri-capable).

### Family B — no perimeter, single-axis state (interior only)

`SDVFAST`, `SDVFSW`

### Capability consequences

The family determines which enum members are ever reachable. Family B
panels can never use `*PERI*` variants regardless of what their
`xSSrv` reports — the physical installation has no perimeter sensors.

### Verification status

| Panel | Family | Status | Notes |
|-------|--------|--------|-------|
| `SDVECU` | A | **live-verified** (our panel) | Base command set confirmed via production use. |
| `SDVECUD` | A | unconfirmed | Likely variant of SDVECU. Add after first probe. |
| `SDVECUW` | A | unconfirmed | Likely variant of SDVECU. Add after first probe. |
| `SDVECU-D` | A | unconfirmed | Dashed form — observe API behaviour. |
| `SDVECU-W` | A | unconfirmed | Dashed form — observe API behaviour. |
| `MODPRO` | A | unconfirmed | No probe observed yet. |
| `SDVFAST` | B | **probe only** (issue #3) | `PERI` inactive confirms family B. Awaits live arm confirmation. |
| `SDVFSW` | B | unconfirmed | No probe observed yet. |

### SDVFAST predicted command set (issue #3)

- Arm: `ARM1`, `ARMDAY1`, `ARMNIGHT1`, `ARMINTFPART1`,
  `ARMPARTFINTDAY1`, `ARMPARTFINTNIGHT1`
- Disarm: `DARM1`
- Rejected: every `*PERI*` variant, `*ANNEX*` variant

## Capture methodology — automated

Run [`scripts/dissect-web-bundle.sh`](../../scripts/dissect-web-bundle.sh).
It auto-detects the latest bundle version from
`customers.verisure.it/owa-static/login`, downloads the main chunk,
and prints:

- The panel enum + family classifier (peri-capable vs not)
- `ArmCodeRequest` wire values
- `DisarmCodeRequest` wire values
- The decoded target-state→command resolver function

No auth required — the bundle is public-facing static JS. Re-run on
each Verisure release to catch schema drift early (new panel codes,
new enum members).

The captured bundle is pinned on disk at `/tmp/verisure-web-bundle/`
for diffing against previous versions.
