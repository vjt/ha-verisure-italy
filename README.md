<p align="center">
  <img src="https://raw.githubusercontent.com/vjt/ha-verisure-italy/master/custom_components/verisure_italy/brand/icon@2x.png" alt="Verisure Italy" width="128">
</p>

# Verisure Italy for Home Assistant

Home Assistant custom component for **Verisure Italy** alarm systems.

Talks directly to `customers.verisure.it/owa-api/graphql`. Fully
replaces the Verisure mobile app for alarm control and camera monitoring.

> **Not affiliated with Verisure Group or Securitas Direct.**

<p align="center">
  <img src="https://raw.githubusercontent.com/vjt/ha-verisure-italy/master/docs/screenshots/06-dashboard.png" alt="Verisure Dashboard" width="600">
</p>

## Features

- **Alarm control** — arm home (partial+perimeter), arm away (total+perimeter), disarm
- **Force arm** — open zone detection with one-tap force-arm and cancel buttons on the dashboard
- **Cameras** — auto-discovered, parallel on-demand capture with per-camera and capture-all buttons
- **Auto-managed dashboard** — Lovelace dashboard auto-populated with alarm panel, camera grid, and capture buttons
- **Passive polling** via xSStatus — no panel ping, no timeline spam
- **Configurable** — poll interval, operation timeout, and poll delay tunable from the UI
- **Config flow** with 2FA/OTP support

## Installation (HACS)

1. Open **HACS** in Home Assistant
2. Click **...** (top right) → **Custom repositories**
3. Add `https://github.com/vjt/ha-verisure-italy` as **Integration**
4. Search for "Verisure Italy" and install
5. Restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration → Verisure Italy**

The API client (`verisure-italy`) is installed automatically from
[PyPI](https://pypi.org/project/verisure-italy/).

## Setup

<details>
<summary>Step-by-step setup screenshots</summary>

### 1. Find the integration

<img src="https://raw.githubusercontent.com/vjt/ha-verisure-italy/master/docs/screenshots/01-setup-search.png" alt="Search for Verisure Italy" width="400">

### 2. Enter credentials

<img src="https://raw.githubusercontent.com/vjt/ha-verisure-italy/master/docs/screenshots/02-setup-login.png" alt="Login" width="400">

### 3. Select phone for 2FA

<img src="https://raw.githubusercontent.com/vjt/ha-verisure-italy/master/docs/screenshots/03-setup-2fa-phone.png" alt="Phone selection" width="400">

### 4. Enter SMS code

<img src="https://raw.githubusercontent.com/vjt/ha-verisure-italy/master/docs/screenshots/04-setup-2fa-code.png" alt="SMS code" width="400">

### 5. Devices are created

<img src="https://raw.githubusercontent.com/vjt/ha-verisure-italy/master/docs/screenshots/05-setup-devices.png" alt="Devices created" width="400">

### 6. Integration page

<img src="https://raw.githubusercontent.com/vjt/ha-verisure-italy/master/docs/screenshots/07-integration-page.png" alt="Integration page" width="400">

</details>

## Dashboard

The integration automatically creates a **Verisure** dashboard in the
sidebar on setup. It's populated with the alarm panel, camera grid, and
capture buttons based on discovered entities. The dashboard updates
itself on every integration reload and is removed when the integration
is unloaded.

## Alarm State Mapping

| Panel State | Proto | HA State | Action |
|---|---|---|---|
| Disarmed | `D` | Disarmed | disarm |
| Partial + Perimeter | `B` | Armed Home | arm_home |
| Total + Perimeter | `A` | Armed Away | arm_away |
| Perimeter only | `E` | Armed Custom Bypass | display only |
| Partial (no peri) | `P` | Armed Custom Bypass | display only |
| Total (no peri) | `T` | Armed Custom Bypass | display only |

## Entity IDs

| Entity | Example ID |
|---|---|
| Alarm panel | `alarm_control_panel.verisure_alarm` |
| Camera | `camera.verisure_fotocucina` |
| Capture button | `button.verisure_fotocucina_capture` |
| Capture all | `button.verisure_capture_all_cameras` |
| Force arm | `button.verisure_force_arm` |
| Cancel force arm | `button.verisure_cancel_force_arm` |

## Force Arm

When arming fails because a zone is open (e.g. a window), the
integration detects the exception and:

1. Shows a persistent notification listing the open zones
2. Fires a `verisure_italy_arming_exception` event (for automations)
3. Makes the **Force Arm** and **Cancel Force Arm** buttons available
   on the dashboard

Tap **Force Arm** to arm anyway, bypassing the open zones. Tap
**Cancel** to abort and revert to disarmed. The buttons disappear
automatically once used or after 2 minutes.

The force arm button exposes `open_zones` and `mode` as state
attributes, usable in automations and templates.

> **Note:** Force arm requires an **administrator** API user.
> A restricted user will arm regardless of open zones without
> raising exceptions — and sensors will trip.

## Smoke Test

After a Home Assistant update, run the smoke test to verify
everything still works:

```bash
./scripts/smoke_test.sh
```

Checks all entities, services, and the dashboard panel are
registered and responding. Takes about 3 seconds, does not
arm or disarm.

## Stability

The integration uses stable HA public APIs for entities
(`CoordinatorEntity`, `ButtonEntity`, `AlarmControlPanelEntity`)
and is expected to survive HA updates without changes.

The **auto-managed dashboard** uses `LovelaceStorage` internals
and `frontend.async_register_built_in_panel`. If a HA update
breaks the dashboard, the alarm, cameras, and buttons still work
— you just won't get the auto-generated sidebar panel. Build a
manual dashboard as a fallback.

The **Verisure API** (`customers.verisure.it`) is the real risk
factor — GraphQL schema or auth flow changes are outside our
control. The API client is a separate package
([verisure-italy](https://pypi.org/project/verisure-italy/))
to isolate those changes.

## Architecture

- [Client library architecture](docs/architecture-client.md) — API client internals, auth flow, state model, exception hierarchy
- [Integration architecture](docs/architecture-integration.md) — HA entities, coordinator, force-arm state machine, security model
- [Hacking guide](docs/hacking.md) — dev setup, deploy workflow, engineering rules, API gotchas, releasing
- [Example automations](docs/automations.md) — arm on leave, safety net, force-arm, night arm, morning disarm, actionable disarm notification, unknown state alert

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

pytest tests/ -x -q
pyright verisure_italy/ custom_components/
ruff check verisure_italy/ tests/ custom_components/
```

## License

MIT. See [LICENSE](LICENSE).
