# Hacking Guide

Development notes for working on `ha-verisure-italy`.

## Prerequisites

- Python 3.12+
- A Verisure Italy account with an **admin** API user
- Access to a Home Assistant instance (HAOS recommended)

## Setup

```bash
git clone git@github.com:vjt/ha-verisure-italy.git
cd ha-verisure-italy

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Project Layout

```
ha-verisure-italy/
├── verisure_italy/          # API client library (published to PyPI)
│   ├── client.py            # VerisureClient — all API operations (1227 LOC)
│   ├── models.py            # Pydantic models — the type boundary (431 LOC)
│   ├── exceptions.py        # One exception per failure mode
│   ├── graphql.py           # GraphQL query/mutation strings
│   └── responses.py         # Response envelope parsing
│
├── custom_components/
│   └── verisure_italy/      # HA integration (installed via HACS)
│       ├── coordinator.py   # Polling + shared state (408 LOC)
│       ├── alarm_control_panel.py  # Alarm entity + force-arm (359 LOC)
│       ├── config_flow.py   # Config + options + reconfigure (384 LOC)
│       ├── dashboard.py     # Auto-managed Lovelace panel (267 LOC)
│       ├── button.py        # Capture + force-arm buttons (232 LOC)
│       └── camera.py        # Camera entities (114 LOC)
│
├── tests/                   # pytest test suite
│   ├── test_client.py       # Client integration tests (1035 LOC)
│   ├── test_camera.py       # Camera model + capture tests (717 LOC)
│   ├── test_architecture.py # AST-enforced type constraints (244 LOC)
│   ├── test_models.py       # Pydantic model tests (211 LOC)
│   └── test_state_mapping.py
│
├── scripts/
│   └── smoke_test.sh        # Post-HA-update verification
│
└── docs/
    ├── architecture-client.md
    ├── architecture-integration.md
    └── hacking.md            # You are here
```

## Running checks

```bash
# Tests — mock at the HTTP boundary, real dependencies inside
pytest tests/ -x -q

# Type checking — strict mode, zero errors
pyright verisure_italy/ custom_components/

# Linting
ruff check verisure_italy/ tests/ custom_components/
```

All three must pass. No exceptions.

## Deploying to HA for development

HAOS doesn't have scp. Deploy via SSH pipe:

```bash
# Single file
ssh root@homeassistant -p 22222 \
  "cat > /mnt/data/supervisor/homeassistant/custom_components/verisure_italy/coordinator.py" \
  < custom_components/verisure_italy/coordinator.py

# All integration files
for f in custom_components/verisure_italy/*.py; do
  ssh root@homeassistant -p 22222 \
    "cat > /mnt/data/supervisor/homeassistant/$(basename $f)" \
    < "$f"
done
```

After deploying:

- **Existing module changed:** `ha core restart` (module cache)
- **New entity/platform:** `ha core restart`
- **Config/options change only:** reload integration via HA API

```bash
# Reload integration (no restart)
source .env
curl -X POST "http://homeassistant:8123/api/services/homeassistant/reload_config_entry" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "alarm_control_panel.verisure_alarm"}'
```

## Engineering rules

These are enforced by code review, architecture tests, and the
Porco Dio Principle (profanity correlates with code quality).

### Security first

This is an alarm system. One wrong behavior = physical security
breach. Design decisions favor correctness over convenience.

- **Unknown state = crash.** Never default to DISARMED. If the panel
  reports something we don't understand, `UnexpectedStateError` fires
  and a human gets notified.
- **Timeout = fail-secure.** `OperationTimeoutError` means "we don't
  know." Assume previous state is still active.
- **No silent fallthrough.** Every exception is specific. Handle it
  explicitly or let it propagate.

### Type discipline

- Pydantic `BaseModel` for all structured types (not `@dataclass`)
- `StrEnum` for constants (not dicts)
- Type annotations on every signature
- `pyright --strict` with zero errors
- AST tests enforce: no `Any`, no bare `dict`, no `object` annotations

### Parse at the boundary

JSON from the Verisure API gets parsed into Pydantic models at the
HTTP layer. If it doesn't match, `ValidationError` blows up right
there. Inside the codebase, types guarantee correctness. No `.get()`
with fallbacks on data that must exist.

### Error handling

- Never swallow exceptions
- No default arguments that create silent degradation paths
- State the contract: "Returns X or raises Y"
- `# type: ignore` requires a justification comment

### Testing

- Assert outcomes, not call sequences
- Mock at the HTTP boundary (`aioresponses`), real Pydantic models
- Never hardcode expected strings — use production code
- Never weaken production code to make tests pass

## Working with the Verisure API

### Upstream reference

The GraphQL schema is reverse-engineered. The closest public reference
is [guerrerotook/securitas-direct-new-api](https://github.com/guerrerotook/securitas-direct-new-api).
We cherry-pick knowledge, not code — their schema helped identify
query names and variable shapes.

### Discovering new API features

1. Open `customers.verisure.it` in Chrome DevTools (Network tab)
2. Perform the action in the webapp
3. Copy the GraphQL request as cURL
4. Add the query to `graphql.py`, response model to `responses.py`
5. Write a client method in `client.py`

### API gotchas

- **Polling model:** Arm/disarm operations return a `reference_id`.
  You must poll `xSArmStatus`/`xSDisarmStatus` until DONE or ERROR.
  The panel processes commands asynchronously.
- **Force-arm needs both IDs:** `forceArmingRemoteId` AND `suid`.
  The webapp sends both. Missing `suid` causes silent failure.
- **RESTRICTED vs ADMIN:** Restricted users don't get
  `ArmingExceptionError` on open zones — the panel arms anyway and
  sensors trip. Use an admin user.
- **WAF (Incapsula):** Aggressive rate limiting. Back off on
  `WAFBlockedError`. Camera captures are especially sensitive.
- **Camera capture:** The panel physically activates the camera.
  Each capture creates a timeline entry in the Verisure app. Don't
  poll aggressively — stagger with 2s delays.

## Releasing

1. Update `CHANGELOG.md`
2. Bump version in `pyproject.toml` and `manifest.json`
3. Commit, tag, push:
   ```bash
   git add -A && git commit -m "chore: bump to X.Y.Z"
   git tag -a vX.Y.Z -m "vX.Y.Z — summary"
   git push origin master --tags
   ```
4. Build and publish to PyPI:
   ```bash
   rm -rf dist/
   python3 -m build
   source .env
   TWINE_USERNAME=__token__ TWINE_PASSWORD="$PYPI_TOKEN" \
     python3 -m twine upload dist/*
   ```
5. Create GitHub release via `gh release create`
6. HACS picks up the new version automatically

**Release does NOT deploy.** Users install via HACS and update at
their own pace.

## Commit style

- `feat:` — new feature or entity
- `fix:` — bug fix
- `perf:` — performance improvement
- `chore:` — version bumps, CI, docs
- `docs:` — documentation only

One logical change per commit. Message explains **why**, not what.
