# Client Library Architecture

`verisure_italy` — async Python client for the Verisure Italy GraphQL API.

## Module Structure

```
verisure_italy/
├── __init__.py      # Public API surface (re-exports)
├── client.py        # VerisureClient — all API operations
├── models.py        # Pydantic models — the type boundary
├── exceptions.py    # One exception per failure mode
├── graphql.py       # GraphQL query/mutation strings
└── responses.py     # Response envelope parsing
```

## Data Flow

```mermaid
sequenceDiagram
    participant C as VerisureClient
    participant H as aiohttp
    participant V as Verisure API
    participant M as Pydantic Models

    C->>H: POST GraphQL mutation/query
    H->>V: HTTPS (customers.verisure.it)
    V-->>H: JSON response
    H-->>C: Raw dict
    C->>M: Parse into typed model
    Note over M: ValidationError if<br/>shape doesn't match
    M-->>C: Typed result
```

## Authentication

```mermaid
sequenceDiagram
    participant C as Client
    participant API as Verisure API

    C->>API: xSLoginToken(user, pass, device_id)
    alt Success
        API-->>C: JWT token + capabilities JWT
    else 2FA required
        API-->>C: TwoFactorRequiredError
        C->>API: xSSendOTP(phone_id)
        API-->>C: SMS sent
        C->>API: xSValidateDevice(code)
        API-->>C: Device validated
        C->>API: xSLoginToken (retry)
        API-->>C: JWT token
    end
    Note over C: Token stored, auto-refreshed<br/>on SessionExpiredError
```

- JWT tokens are EdDSA-signed, per-installation capabilities
- Device registration is permanent — 2FA is only needed once per `device_id`
- Token refresh is `asyncio.Lock`-protected to prevent concurrent re-auth races

## Alarm State Model

Two axes, six protocol states. No defaults, no fallbacks.

```mermaid
graph LR
    subgraph Interior
        OFF[OFF]
        PARTIAL[PARTIAL<br/>shock sensors]
        TOTAL[TOTAL<br/>shock + PIR]
    end

    subgraph Perimeter
        P_OFF[OFF]
        P_ON[ON]
    end

    OFF --- P_OFF -->|D| DISARMED
    OFF --- P_ON -->|E| PERIMETER_ONLY
    PARTIAL --- P_OFF -->|P| PARTIAL_ONLY
    PARTIAL --- P_ON -->|B| PARTIAL_PERIMETER
    TOTAL --- P_OFF -->|T| TOTAL_ONLY
    TOTAL --- P_ON -->|A| TOTAL_PERIMETER
```

| Proto | Interior | Perimeter | Real-world use |
|-------|----------|-----------|----------------|
| `D` | OFF | OFF | Home, everything off |
| `B` | PARTIAL | ON | Home, shock + perimeter |
| `A` | TOTAL | ON | Away, everything armed |
| `E` | OFF | ON | Perimeter only (rare) |
| `P` | PARTIAL | OFF | Partial only (rare) |
| `T` | TOTAL | OFF | Total only (rare) |

Unknown proto codes raise `UnexpectedStateError` — never silently default.

## Arm / Disarm Flow

```mermaid
sequenceDiagram
    participant C as Client
    participant API as Verisure API

    C->>API: xSArmPanel(target_state)
    API-->>C: reference_id + WAIT status

    loop Poll until DONE or ERROR
        C->>API: xSArmStatus(reference_id)
        API-->>C: status
    end

    alt DONE
        C-->>C: Return success
    else ERROR
        C-->>C: Raise OperationFailedError
    else TIMEOUT
        C-->>C: Raise OperationTimeoutError
    end
```

## Force-Arm Flow

When arming is blocked by open zones (admin users only):

```mermaid
sequenceDiagram
    participant C as Client
    participant API as Verisure API

    C->>API: xSArmPanel(target_state)
    API-->>C: reference_id + WAIT

    C->>API: xSArmStatus(reference_id)
    API-->>C: NON_BLOCKING + allowForcing

    C->>API: xSGetExceptions(reference_id)
    API-->>C: Zone list (alias, zone_id, type)

    Note over C: Raise ArmingExceptionError<br/>(reference_id, suid, zones)

    C->>API: xSArmPanel(target_state,<br/>forceArmingRemoteId, suid)
    API-->>C: reference_id + WAIT

    loop Poll
        C->>API: xSArmStatus(reference_id)
    end
    API-->>C: DONE (armed, zones bypassed)
```

## Camera Capture

```mermaid
sequenceDiagram
    participant C as Client
    participant API as Verisure API
    participant CDN as Verisure CDN

    C->>API: xSRequestImages(camera)
    API-->>C: reference_id

    loop Poll
        C->>API: xSRequestImagesStatus(reference_id)
        API-->>C: status + image data
    end

    Note over C: Base64 JPEG decoded,<br/>validated (FFD8 header)

    opt Full resolution upgrade
        C->>API: xSGetThumbnail(camera)
        API-->>C: id_signal
        C->>API: xSGetPhotoImages(id_signal)
        API-->>C: Full-res JPEG
    end
```

## Exception Hierarchy

```
VerisureError
├── AuthenticationError        # Bad credentials / locked account
├── TwoFactorRequiredError     # Device needs 2FA
├── SessionExpiredError        # JWT expired
├── APIResponseError           # GraphQL error (has http_status)
├── APIConnectionError         # Network failure
├── WAFBlockedError            # Incapsula WAF block
├── UnexpectedStateError       # Unknown proto code (SECURITY)
├── SameStateError             # Benign race — panel already in target state
├── StateNotObservedError      # Arm/disarm before first xSStatus
├── OperationTimeoutError      # Arm/disarm poll timeout
├── OperationFailedError       # Panel rejected operation
├── UnsupportedPanelError      # Panel not on SUPPORTED_PANELS allowlist
├── UnsupportedCommandError    # Panel's active services lack the command
├── ImageCaptureError          # Capture timeout / invalid data
└── ArmingExceptionError       # Open zones (has reference_id, suid, zones)
```

Every exception is specific. No generic catch-alls. Callers handle
what they can, let the rest propagate to generate human-visible
notifications.

## Security Properties

- **No silent failures.** Unknown proto codes crash with `UnexpectedStateError`, not a default state. If the alarm reports something we don't understand, a human gets notified.
- **Fail-secure.** `OperationTimeoutError` means "we don't know if it worked." The HA alarm entity responds by going UNKNOWN and requesting a forced refresh — it does NOT silently revert to the prior state. Direct client callers should do the same.
- **Parse at the boundary.** All API responses are parsed into Pydantic models immediately. `ValidationError` inside = bug in Verisure's API. No dicts or `Any` past the HTTP layer.
- **No credentials in memory longer than needed.** Password is used for login only, not stored after token acquisition.
- **Token refresh is atomic.** `asyncio.Lock` prevents concurrent refresh races that could leave the client in an inconsistent auth state.
- **Admin vs Restricted.** Force-arm (open zone bypass) only works with admin API users. Restricted users arm regardless of open zones — sensors will trip. This is a Verisure API design choice, not ours.
