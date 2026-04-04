# ha-verisure — Project Memory

## What This Is
Home Assistant custom component for Verisure Italy alarm systems.
Talks to `customers.verisure.it/owa-api/graphql`. Controls arm/disarm,
reads alarm state, replaces the Verisure mobile app entirely.

**This is security software.** One wrong behavior = disarmed alarm =
thief gets in. Every design decision optimizes for correctness over
convenience.

## Architecture
- **API client** (`verisure_api/`): typed GraphQL client for Verisure
  IT. Pydantic models for all request/response types. Parses at the
  boundary, crashes on unexpected data. Structurally diffable against
  upstream (github.com/guerrerotook/securitas-direct-new-api) for when
  Verisure changes their API — we cherry-pick knowledge, not code.
- **HA integration** (`custom_components/verisure_it/`): alarm control
  panel, sensors, config flow. Our code, clean, Italy-specific.
- **State machine**: two-axis model (interior mode × perimeter on/off).
  Valid transitions enumerated. Unknown states = ERROR, not defaults.

## Tech Stack
- **Python 3.12**, async (aiohttp)
- **Pydantic** for all models
- **pytest** — `pytest tests/ -x -q`
- **pyright** — strict mode
- **ruff** — linter + formatter

## Engineering Standards

### This Is Security Software
- **Fail-secure, not fail-safe.** Unknown state = ERROR, never
  "probably disarmed." Disarm failure = assume still armed. Poll
  crash = keep last known state + notify human.
- **Crash loud on unexpected input.** Unknown proto codes, missing
  fields, unexpected API responses all raise exceptions that generate
  human-visible HA notifications. No silent fallthrough.
- **Explicit state machine.** Valid transitions enumerated. Anything
  outside the model is an error.
- **Audit trail.** Every arm/disarm action logged with timestamp,
  source, command sent, proto response received, resulting state.
- **No "smart" behavior.** Pedantic correctness over convenience.
- **No HA-level disarm PIN.** By design — HA is authenticated, runs
  in an encrypted VM behind the home network perimeter. The Verisure
  API itself requires credentials. Adding a PIN would break
  automations (auto-disarm). Patches welcome for optional PIN support.

### Type System
- **Pydantic models only.** No `@dataclass`. All structured types
  use Pydantic `BaseModel`.
- **Type annotations on all signatures.** No exceptions.
- **Enums over dicts.** Before adding a constant mapping, create a
  StrEnum. Inline dicts drift and duplicate.
- **Type errors are design signals.** When a type constraint blocks
  your approach, the constraint is probably correct — your approach
  is probably wrong.
- **Total consistency or nothing.** Half-typed is worse than untyped.

### Error Handling
- **Never swallow exceptions.** Handle explicitly or let crash.
- **No default arguments.** Every parameter explicit. `= None`
  defaults create silent degradation paths. The only acceptable
  defaults are genuine configuration values (e.g. `timeout=30`)
  where the default is the correct production behavior, not a bypass.
- **No `.get()` with fallbacks** on data that must exist. Parse at
  the boundary, crash inside. If a field is missing, that's a bug
  in the data source — surface it, don't paper over it.
- **State the contract.** Signature + failure mode in one sentence
  before implementing: "Returns X or raises Y."

### Architecture
- **Constructor injection.** No global state, no singletons.
- **No leaky abstractions.** Each layer owns its domain. Return
  domain types, not strings/dicts callers parse.
- **Parse at the boundary.** JSON dicts from the Verisure API get
  parsed into Pydantic models at the HTTP layer. If parsing fails,
  blow up there. Inside the codebase, types guarantee correctness.
- **One feature, one code path.** Implement once, reuse everywhere.
  Never copy-paste with tweaks.
- **Fix root causes, not examples.** No band-aids, no
  `filterwarnings`, no `# type: ignore` without justification.

### Code Quality
- **Read before writing.** Grep for what you're about to build.
- **Challenge the spec.** If domain knowledge contradicts the
  requirements, say so before building.
- **Debug with data first.** Read logs, inspect state before
  changing code. NEVER guess. Evidence first.
- **Never fabricate explanations.** "I don't know, let me check"
  beats a confident wrong answer.
- **"Done" means done.** Every caller updated, every test fixed.
  Grep for stale references before declaring complete.
- **Bite-sized commits.** One logical change. Messages explain WHY.

### Testing
- Assert outcomes, not call sequences.
- Mock at boundaries (Verisure API), real dependencies inside.
- Use production code in tests — never hardcode expected strings.
- Never weaken production code to make tests pass.

## Verisure IT Protocol Reference

### Proto Response Codes (confirmed from live panel SDVECU)
| Code | API Message | Interior | Perimeter | Confirmed |
|------|-------------|----------|-----------|-----------|
| `D` | `inactive_alarm` | OFF | OFF | yes |
| `E` | `active_perimetral_alarm_msg` | OFF | ON | yes |
| `P` | `armed_partial` | PARTIAL | OFF | yes |
| `B` | `armed_partial_plus_perimeter` | PARTIAL | ON | yes |
| `T` | (unconfirmed) | TOTAL | OFF | |
| `A` | `active_perimeter_plus_alarm` | TOTAL | ON | yes |

Night modes (`Q`, `C`) do not exist on this panel.

### Two-Axis State Model
Interior: OFF | PARTIAL (shock sensors) | TOTAL (shock + interior sensors)
Perimeter: OFF | ON

Six protocol-level states. Three are real-world usage:
- **Disarmed** (D): OFF/OFF — home, all off
- **Partial + Perimeter** (B): PARTIAL/ON — home, shock + perimeter
- **Total + Perimeter** (A): TOTAL/ON — away, everything armed

The other three (E=peri only, P=partial only, T=total only) are
valid protocol states — we parse and display them accurately but
don't offer them as primary actions in the HA UI.

Unknown proto codes are errors, not defaults.

### API Endpoints
- Base: `https://customers.verisure.it/owa-api/graphql`
- Auth: JWT tokens (EdDSA signed), capabilities JWT per installation
- Operations: GraphQL mutations/queries (xSLoginToken, xSArmPanel,
  xSDisarmPanel, xSCheckAlarm, xSCheckAlarmStatus, xSStatus, etc.)

### Panel Details
- Panel type: SDVECU (with perimeter sensors)
- Services available: IMG, EST, ARM, DARM, ARMNIGHT, CAMERAS,
  TIMELINE(id=506), DEACTIVATEZONE, CONNSTATUS, plus others
- No sentinel/CONFORT sensors, no DOORLOCK

## Access
- SSH to HAOS: `ssh root@homeassistant -p 22222`
- SSH to HA container: `ssh hassio@homeassistant` (no scp, tar over ssh)
- Deploy path: `/mnt/data/supervisor/homeassistant/custom_components/`
  via root SSH (files are root-owned, hassio can't write)
- Reload: `ha core restart` via root SSH, or better: reload integration
  via HA API
