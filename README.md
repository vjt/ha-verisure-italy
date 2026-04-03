# Verisure Italy for Home Assistant

Home Assistant custom component for **Verisure Italy** alarm systems.

Talks directly to `customers.verisure.it/owa-api/graphql`. Fully
replaces the Verisure mobile app for alarm control and monitoring.

> **Not affiliated with Verisure Group or Securitas Direct.**

## Installation (HACS)

1. Open **HACS** in Home Assistant
2. Click **...** (top right) → **Custom repositories**
3. Add `https://github.com/vjt/ha-verisure-italy` as **Integration**
4. Search for "Verisure Italy" and install
5. Restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration → Verisure Italy**

The API client (`verisure-italy`) is installed automatically from
[PyPI](https://pypi.org/project/verisure-italy/).

## Features

- **Passive polling** via xSStatus — no panel ping, no timeline spam
- **Arm/disarm** — partial+perimeter (home), total+perimeter (away)
- **Force arm** — open zone detection with `verisure_italy_arming_exception`
  event and `verisure_italy.force_arm` service
- **Config flow** with 2FA/OTP support
- **Configurable poll interval** (default 5 seconds)

## Alarm State Mapping

| Panel State | Proto | HA State | Action |
|---|---|---|---|
| Disarmed | `D` | Disarmed | disarm |
| Partial + Perimeter | `B` | Armed Home | arm_home |
| Total + Perimeter | `A` | Armed Away | arm_away |
| Perimeter only | `E` | Armed Custom Bypass | display only |
| Partial (no peri) | `P` | Armed Custom Bypass | display only |
| Total (no peri) | `T` | Armed Custom Bypass | display only |

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

pytest tests/ -x -q                                # 118 tests
pyright verisure_italy/ custom_components/          # strict mode, 0 errors
ruff check verisure_italy/ tests/ custom_components/
```

## License

MIT. See [LICENSE](LICENSE).
