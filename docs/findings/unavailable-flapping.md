# Alarm "unavailable" — flapping and sticky outages

## Symptoms

Three distinct failure modes observed on 2026-04-20:

**A. Brief flapping (~8×/day)**: `alarm_control_panel.verisure_alarm`
transitions to `unavailable` for a single poll tick (~5s), recovers
next tick. Annoying but survivable. **Fixed in v0.8.4.**

**B. Multi-hour sticky outage**: on 2026-04-20, alarm was unavailable
from 10:11 UTC to 16:04 UTC (~6h). Integration completely dormant —
zero `verisure_italy` log lines in the gap. Recovery required a
manual HA core restart. **Unacceptable for security software. Fixed
in v0.8.4.**

**C. Stale capabilities loop**: on 2026-04-20 21:07 UTC, alarm went
unavailable and every subsequent 5s poll raised `SessionExpiredError`
from the `Status` operation. The coordinator's `except
SessionExpiredError` handler called `login()` and retried, but the
retry kept hitting `SessionExpiredError` too — so the error escaped
all handlers and logged as "Unexpected error fetching
verisure_italy data" on every tick. Integration cycled in this state
until a manual restart. **Fixed in v0.8.5.**

## Root cause

### Mode B — sticky outage: overbroad `AuthenticationError` classification

At 10:11 UTC the Verisure server returned a GraphQL error with the
message `Login failed: Cannot read properties of undefined (reading 'it')`.
This is an **upstream Node.js bug** (classic `undefined.it` JavaScript
error) — credentials were intact, this was a transient server-side
failure in Verisure's own backend.

Our client's `login()` method (`verisure_italy/client.py:493-496`)
catches **any** `APIResponseError` during login and re-raises it as
`AuthenticationError`:

```python
except APIResponseError as err:
    raise AuthenticationError(
        f"Login failed: {err.message}"
    ) from err
```

The coordinator (`custom_components/verisure_italy/coordinator.py:261-262`)
then maps `AuthenticationError` → `ConfigEntryAuthFailed`, which tells
HA to unload the integration and wait for the user to click the
Repair card. **HA does not auto-retry `ConfigEntryAuthFailed`** — it
is designed for confirmed credential problems.

Result: a transient upstream JS bug locks the integration out for
hours until the user notices and intervenes. Security software goes
dormant silently — the worst possible failure mode.

### Mode A — brief flapping: zero transient-error tolerance

On any `APIConnectionError` (TCP reset, DNS hiccup) or 5xx
`APIResponseError`, the coordinator raises `UpdateFailed` on the
first attempt. `last_update_success` flips to `False`, the entity
goes `unavailable` for one poll cycle (~5s), then recovers.

Verisure's backend is visibly flaky — logs show ~8 `ECONNRESET` /
500 / `status_inventory_error` per day originating from
`verisureservicesecuritylayer-svc.owa-ns` (their Kubernetes
namespace name leaks in error messages). Each of these briefly
flags our entity unavailable.

### Mode C — stale capabilities loop

The Verisure API uses **two separate JWTs per authenticated call**:
`_auth_token` (from `login()`) and `_capabilities[installation]`
(from `get_services()`). Both are load-bearing: any authenticated
operation sends both, and the server rejects the call if either one
is invalid — returning a 403 that `_check_graphql_errors` maps to
`SessionExpiredError`.

`_ensure_auth` refreshes each token independently, based on the
local JWT `exp` claim (with a 1-minute safety margin). This is the
bug: **the server can invalidate the session while the local `exp`
claim still says "valid"** (session purge, user logged in elsewhere,
backend restart, infrastructure rotation). In that case:

1. Poll tick N: `Status` fails with `SessionExpiredError`
   (server rejects the stale capabilities).
2. Coordinator catches it → calls `client.login()`. `login()`
   succeeds and installs a fresh `_auth_token`.
3. Coordinator retries `get_general_status()` → `_ensure_auth`
   sees `_capabilities_exp` still in the future → **skips
   `get_services()` refresh** → sends the same stale capabilities
   → server rejects again → second `SessionExpiredError`.
4. The second error is raised from inside the coordinator's
   `except SessionExpiredError` block, so it escapes every handler,
   falls through to `DataUpdateCoordinator`'s generic `Exception`
   catcher, and logs as `Unexpected error fetching verisure_italy
   data`.
5. Entity `unavailable`, next tick repeats step 1 forever.

No `ConfigEntryAuthFailed` fires (v0.8.4's Mode B fix holds), no
retry absorbs the error (wrong layer — this is a GraphQL-level
session rejection, not an HTTP blip). The integration is stuck
until an HA restart clears in-memory token state.

## Design constraints

- **No SMS without user approval.** Confirmed from operational
  history: `login()` calls during normal polling have never triggered
  a 2FA SMS. After the initial device validation, the stored
  `device_id` / `uuid` / `id_device_indigitall` identify the client
  as a trusted device, and `login()` succeeds without OTP. This means
  **auto-retrying `login()` is safe** — it will not spam the user's
  phone. 2FA is only triggered on first device setup or explicit
  trust revocation, both of which require user action via the Repair
  card regardless.

- **Fail-secure preserved.** After retries are exhausted, a genuine
  persistent failure must still mark the entity `unavailable` (Mode A
  residual behavior) or trigger `ConfigEntryAuthFailed` (Mode B
  genuine auth failure). We are not suppressing errors — we are
  absorbing transient blips that would otherwise leak into entity
  state.

- **No binary sensor.** "Unavailable since 5'" is expressible as a
  native HA automation trigger with `for: "00:05:00"`. No extra
  entity needed.

## Fix

### 1. Retry transient HTTP errors at the client layer

Add exponential-backoff retry inside the client's HTTP transport for
genuinely transient failures. Retries happen **inside a single
coordinator tick** — the coordinator sees either success (no state
change) or a persistent failure (`UpdateFailed` as today).

- **Retry**: `APIConnectionError`, `APIResponseError` with
  `http_status >= 500`.
- **Do not retry**: 4xx client errors, `WAFBlockedError` (WAF
  semantics demand a cold-off), `SessionExpiredError` (different
  recovery path), `TwoFactorRequiredError`, `AuthenticationError`,
  `ValidationError`.
- **Schedule**: up to 3 attempts total. Delays: 0s, 5s + jitter,
  10s + jitter. Capped at 30s per delay for future tuning. Jitter:
  ±20% of base delay.
- **Logging**: structured WARNING on each retry —
  `"Transient %s on %s, retry %d/3 in %.1fs: %s"`.

### 2. Stop over-classifying login failures as auth errors

Remove the blanket `APIResponseError → AuthenticationError`
conversion in `client.py:493-496`. Let `APIResponseError` propagate
from `login()`. The coordinator already maps it correctly to
`UpdateFailed` via the existing handler at `coordinator.py:263-264`.

`AuthenticationError` will continue to be raised **only** for cases
that are definitively credential-related:
- `result.hash is None` after a successful HTTP response
- JWT decode failure (`_decode_jwt_expiry`)
- JWT missing `exp` claim

`TwoFactorRequiredError` is still raised on explicit 2FA signals and
still maps to `ConfigEntryAuthFailed` — that is correct, 2FA requires
user action.

If we later identify a specific GraphQL-error shape that reliably
indicates "credentials rejected" (HTTP 401, or a known error code),
we can reintroduce targeted classification — but the current
`APIResponseError` catch is too broad to distinguish.

### 3. Invalidate cached tokens on `SessionExpiredError` (v0.8.5)

When `_check_graphql_errors` raises `SessionExpiredError`, the client
wraps it in `_execute` to nuke both cached tokens before propagating:

```python
try:
    self._check_graphql_errors(response_text, operation)
except SessionExpiredError:
    self._auth_token = None
    if installation is not None:
        self._capabilities.pop(installation.number, None)
        self._capabilities_exp.pop(installation.number, None)
    raise
```

The next `_ensure_auth` call sees both tokens missing (or the auth
token missing for installation-less operations) and does a full
refresh: `login()` + `get_services()`. The coordinator's existing
`except SessionExpiredError → login() + retry` flow now recovers
naturally because `get_general_status()` → `_ensure_auth()` does
the cascaded refresh.

**KISS decision — nuke both, not just the likely culprit.** Auth
and capabilities are tied to the same server session (capabilities
is minted via an authenticated call, so it cannot exist without a
valid auth session). A 403 does not tell us which of the two the
server rejected. Invalidating only capabilities would need
cascading logic for the case where auth is also bad. Invalidating
both costs at most one extra `login()` call per recovery event,
which is negligible and rare.

### 4. Automation trigger (lives in `ha-config` repo)

In `automations.yaml`, add a trigger on prolonged unavailability:

```yaml
- alias: Verisure alarm unavailable for 5 minutes
  trigger:
    - platform: state
      entity_id: alarm_control_panel.verisure_alarm
      to: "unavailable"
      for: "00:05:00"
  action:
    # critical notification to user's phone
```

No notification for glitches below 5 minutes. No spam. Trigger fires
once per sustained outage and clears when the entity recovers.

## Expected net effect

- Mode A: absorbed silently inside coordinator ticks. Entity never
  reports `unavailable` for transient blips. Zero automation
  triggers from brief glitches. ~8 flaps/day → 0.
- Mode B: transient login server bug → retried 3× inside tick. If
  still failing, `UpdateFailed` and entity unavailable. Next
  coordinator tick (5s later) retries fresh. If the backend recovers
  within minutes, entity recovers automatically. The 5-minute
  automation timer catches real sustained outages without triggering
  on single-tick recoveries.
- True auth failure (password changed, device trust revoked): still
  surfaces as `ConfigEntryAuthFailed` → Repair card, user reauth.

## Testing

- Mock Verisure server returns GraphQL `"Cannot read properties..."`
  → client raises `APIResponseError` (not `AuthenticationError`).
  Coordinator raises `UpdateFailed` (not `ConfigEntryAuthFailed`).
- Mock HTTP 500 → retried 3× with backoff, then `APIResponseError`.
  Verify delays via mocked `asyncio.sleep` (no wall-clock waits).
- Mock `ClientConnectorError` on attempt 1, success on attempt 2 →
  single final success surfaces to coordinator, no retry visible.
- Mock 3× HTTP 500 → exhausts retries, raises `APIResponseError`.
- Mock HTTP 401 → `APIResponseError` (not retried, 4xx bucket). Will
  surface as `UpdateFailed` under current mapping — acceptable since
  we have no explicit 401-as-bad-creds signal yet; observation will
  tell us if HA's repair card needs to surface faster.
- `WAFBlockedError` → propagated without retry.
- `result.hash is None` → `AuthenticationError` (still the path for
  actual credential rejection until a better signal is identified).

## References

- HA log excerpts from 2026-04-20 (`docker logs homeassistant`):
  single ERROR line at 12:11:10 local "Authentication failed while
  fetching verisure_italy data: Login failed: Cannot read properties
  of undefined (reading 'it')", followed by 6h of silence, recovery
  at 18:04:31 local on HA core restart.
- `verisure_italy/client.py:229-285` — HTTP transport error mapping.
- `verisure_italy/client.py:461-517` — `login()` method.
- `custom_components/verisure_italy/coordinator.py:244-291` —
  `_async_update_data` exception handling.
- `verisure_italy/exceptions.py` — exception hierarchy.
