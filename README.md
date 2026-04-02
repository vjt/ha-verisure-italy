# ha-verisure

Home Assistant custom component for **Verisure Italy** alarm systems.

Talks directly to `customers.verisure.it/owa-api/graphql`. Goal: fully
replace the Verisure mobile app for alarm control and monitoring.

## Status

**Work in progress.** API client is complete and E2E validated against
live panel. Full arm/disarm cycle confirmed. HA integration layer next.

## Design Principles

This is security software. Wrong behavior = disarmed alarm = thief gets in.

- **Fail-secure.** Unknown state = ERROR, not "probably disarmed"
- **Strong types.** Pydantic models, no `Any`, no dict soup. AST tests enforce it
- **Crash loud.** Unknown proto codes, missing fields, unexpected responses all raise
- **Parse at the boundary.** JSON → Pydantic model in one step. Inside: types guarantee correctness
- **No "smart" behavior.** Pedantic correctness over convenience

## Alarm State Model

Two-axis: **interior mode** × **perimeter**.

| State | Interior | Perimeter | Proto Code | Primary |
|-------|----------|-----------|------------|---------|
| Disarmed | OFF | OFF | `D` | yes |
| Perimeter only | OFF | ON | `E` | |
| Partial | PARTIAL | OFF | `P` | |
| Partial + Perimeter | PARTIAL | ON | `B` | yes |
| Total | TOTAL | OFF | `T` | |
| Total + Perimeter | TOTAL | ON | `A` | yes |

Three primary modes (disarmed, partial+perimeter, total+perimeter).
Six protocol states recognized. Unknown codes crash loud.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

pytest tests/ -x -q          # 88 tests
pyright verisure_api/         # strict mode, 0 errors
ruff check verisure_api/ tests/
```

## License

Private. Not for distribution.
