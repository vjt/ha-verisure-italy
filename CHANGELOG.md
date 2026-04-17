# Changelog

## 0.8.2 — 2026-04-17

Bugfix release — config flow resilience (continued).

### Fixes
- **`Installation` metadata fields accept null** ([#2](https://github.com/vjt/ha-verisure-italy/issues/2)) — Verisure returns `null` for optional installation fields (`name`, `surname`, `address`, `city`, `postcode`, `province`, `email`, `phone`, `type`), crashing the config flow with Pydantic `string_type` errors when picking among multiple installations. These nine fields are pure metadata — the integration only reads `number`, `panel`, `alias` — so they are now `str | None`. The three load-bearing fields stay strict.

## 0.8.1 — 2026-04-17

Bugfix release — config flow resilience.

### Fixes
- **`RawDevice.is_active` accepts null** ([#1](https://github.com/vjt/ha-verisure-italy/issues/1)) — Verisure started returning `isActive: null` for some devices in `xSDeviceList`, breaking setup with a Pydantic `bool_type` validation error. Field is now `bool | None`. Camera filter changed to only skip explicit `False` (null devices treated as active), matching the upstream [guerrerotook/securitas-direct-new-api](https://github.com/guerrerotook/securitas-direct-new-api) behavior — three years of Spain traffic beats our guess.

## 0.8.0 — 2026-04-04

Full codebase review (8 parallel agents, 43 findings). All HIGH and MEDIUM items resolved. Clean pyright (0 errors, 14 seconds). 165 tests.

### Security & Correctness
- **Session recovery in arm/disarm** — `SessionExpiredError` during arm/disarm now triggers re-login + retry instead of failing with "unexpected error" while the panel may have actually armed
- **Honest state during force-arm** — entity shows DISARMED (the truth) instead of lying with ARMING for up to 120s. Force-arm status communicated via dashboard alert banner, buttons, and notifications
- **Deterministic force-arm expiry** — 120s timer via `async_call_later` instead of checking at poll boundaries (was up to 420s with long poll intervals)
- **Unknown state persistent notification** — unknown proto codes now create a persistent notification + fire `verisure_italy_unknown_state` event, not just a log entry
- **Duplicate installation guard** — config flow aborts if the same installation is already configured
- **Arm error details preserved** — non-force-arm errors now surface panel error code and type (was discarded by generic poll machinery)
- **`_last_proto` synced from polls** — first arm/disarm after startup sends real proto code, not empty string
- **PanelError.code and .type required** — malformed error responses crash at parse boundary instead of silently accepting null diagnostics
- **Undeclared GraphQL variables removed** — stopped sending `currentStatus` in queries that don't declare it

### Architecture
- **Arm/disarm moved to coordinator** — entity no longer calls `coordinator.client.*` directly. Session recovery lives in one place
- **`ConfigEntry.runtime_data`** — replaced `hass.data[DOMAIN]` dict pattern with typed `VerisureConfigEntry` for type-safe coordinator access
- **`CameraRefreshable` Protocol** — replaced `list[object]` + `hasattr` duck typing with proper Protocol
- **`_execute_raw` / `_execute` split** — `validate_device` OTP flow no longer re-parses response. One parse pass, no side channels
- **Entry-scoped background tasks** — dashboard setup and thumbnail refresh use `async_create_background_task` for auto-cancellation on unload
- **Thread-safe force context expiry** — timer callback dispatches state writes via `async_create_task`

### UI & UX
- **Buttons hidden from auto-generated dashboard** — all buttons marked as `EntityCategory.DIAGNOSTIC` with `entity_registry_visible_default=False`
- **Dashboard "Arming blocked" banner** — conditional alert card appears when force-arm is pending
- **Reauth strings** — reauth flow steps now have proper labels instead of raw field names
- **services.yaml cleaned** — removed phantom `entity_id` field that handlers ignored
- **CameraGroup sentinel fix** — empty-string sentinel replaced with `None` + explicit check
- **Reauth/reconfigure `.get()` removed** — direct subscript on required config data

### Tests & Tooling
- **7 new tests** — `validate_device` OTP flow, unknown proto code propagation, state map sync cross-check
- **`scripts/check.sh`** — chains pyright + pytest + ruff in one command
- **pyright runs in 14 seconds** — `include` config limits analysis to project sources only

### Docs
- **Example automations expanded** — 8 battle-tested recipes with prerequisites (presence sensors, binary sensor templates)
- **Force-arm screenshot** — dashboard with "Arming blocked" banner
- **Events reference** — `verisure_italy_arming_exception` and `verisure_italy_unknown_state`

### v0.7.0 post-release fixes (included)
- Config flow skips 2FA when device is already validated
- `asyncio.Lock` guards concurrent arm/disarm/force-arm
- `ValidationError` and `TwoFactorRequiredError` caught in coordinator polling
- Use HA's shared `ClientSession` instead of standalone sessions
- Dashboard setup wrapped in try/except with persistent notification on failure
- Disarm status poll preserves error codes from panel
- Camera overlay timestamps use local time, not UTC

## 0.7.0 — 2026-04-04

Security hardening, type safety, HA pattern fixes from full codebase review.

### Added
- **Reauth flow** — HA-initiated credential refresh on `ConfigEntryAuthFailed`, with full 2FA support. No more "remove and re-add" when credentials expire
- `ForceArmContext` Pydantic model — typed force-arm state replaces `dict[str, Any]` across coordinator, alarm entity, and buttons
- `ForceArmable` Protocol — typed `alarm_entity` back-reference replaces `Any`
- `_AlarmOperationBase` base class — eliminates triplicated `proto_code`/`alarm_state`/`is_pending` properties
- Startup assertion that `_STATE_MAP` covers all `PROTO_TO_STATE` values
- `asyncio.gather` exception logging in parallel camera capture

### Changed
- `parse_proto_code` raises `UnexpectedStateError` (was `ValueError` — the coordinator catch was dead code)
- `_STATE_MAP[]` crash-loud on unknown alarm states (was `.get()` returning `None`)
- Arm/disarm/force-arm catch all `VerisureError` + `ValidationError` with state recovery and persistent notification (was missing `APIConnectionError`, `WAFBlockedError`, `SessionExpiredError`)
- `_overlay_text` dispatched via `async_add_executor_job` in `_try_full_image` (was blocking event loop)
- Camera entities and capture buttons inherit `CoordinatorEntity` for proper availability tracking
- Module-level `_CAMERA_ENTITIES` global replaced with `coordinator.camera_entities`
- `VerisureStatusData` converted from `@dataclass` to Pydantic `BaseModel`
- All JWT/auth datetime comparisons use UTC-aware timestamps
- `check_request_images_status` raises `OperationFailedError` on ERROR response (was treating it as "done")
- `_check_graphql_errors` catch-all for unrecognized GraphQL errors (was silently swallowed)
- Dashboard entity matching uses `endswith()` instead of fragile substring contains
- Service schemas cleaned up (removed unused `entity_id` parameter)

### Removed
- Dead `ArmPanelResponse` model (never used, `ArmPanelEnvelope` had its own inline result)

## 0.6.0 — 2026-04-04

Force-arm UX, reconfigure flow, state management overhaul.

### Added
- **Force Arm / Cancel Force Arm button entities** — appear on the dashboard when arming is blocked by open zones. One tap to bypass, one tap to cancel. Auto-hide after use or 2-minute expiry
- Force Arm button exposes `open_zones` and `mode` as state attributes for automation use
- **Reconfigure flow** — change API credentials without removing the integration, with full 2FA support
- Conditional dashboard cards for force-arm buttons (hidden when unavailable)
- Smoke test script (`scripts/smoke_test.sh`) for post-HA-update verification
- INFO/WARNING logging for all arm, disarm, force-arm, and exception flows

### Changed
- Force context moved from alarm entity to coordinator (shared state between alarm and button entities)
- Alarm entity reference stored on coordinator (replaces fragile `entity_components` internal lookup)
- `_arm_in_progress` flag suppresses coordinator state updates during API calls
- Split `_clear_force_context` (immediate, for cancel/error) and `_expire_force_context` (silent, for success paths) to avoid stale-data state flicker
- Stale force context cleared on successful arm/disarm (not just on expiry)

### Fixed
- Force-arm `suid` parameter — Verisure API requires both `forceArmingRemoteId` and `suid`
- Spurious DISARMED→ARMING state transitions during arm operations (caused "Already running" on automations)
- Alarm state no longer flashes to DISARMED between ARMING and force context set

## 0.5.1 — 2026-04-03

### Changed
- Default poll timeout from 30s to 60s
- Auto-managed dashboard title to "Verisure" (was "Verisure Italy")
- Dashboard set as non-editable via frontend panel API

## 0.5.0 — 2026-04-03

Auto-managed dashboard, configurable poll parameters, entity ID cleanup.

### Added
- Auto-managed Lovelace dashboard in the sidebar (self-registering via `frontend.async_register_built_in_panel`)
- "Capture All Cameras" button entity on the alarm device
- Configurable poll interval (3–300s), poll timeout (15–120s), and poll delay (1–10s) from options UI — live-applied without restart
- Sections layout dashboard: alarm panel + capture-all on the left, camera grid on the right

### Changed
- Entity IDs cleaned up: `camera.verisure_{name}`, `button.verisure_{name}_capture`, `button.verisure_capture_all_cameras`
- Device names prefixed with "Verisure" for clear identification
- Dashboard entities grouped by `device_id` from entity registry (not string matching)
- Capture buttons become unavailable during capture (visual feedback)

## 0.4.1 — 2026-04-03

### Fixed
- Image overlay runs in executor to avoid blocking the HA event loop (Pillow font loading does scandir)

## 0.4.0 — 2026-04-03

Camera support for Verisure Italy panels.

### Added
- Camera entities: one per physical camera device (QR, YR, YP, QP types)
- Camera device discovery via xSDeviceList (filters active camera types)
- On-demand image capture via `verisure_italy.capture_cameras` service
- Per-camera capture button entities for one-tap image refresh from UI
- Camera name and timestamp overlay on captured images (Pillow)
- Full-resolution image upgrade when panel supports it (get_photo_images)
- Passive thumbnail refresh on startup from Verisure CDN (no panel ping)
- GraphQL queries: xSDeviceList, xSRequestImages, xSRequestImagesStatus, xSGetThumbnail, xSGetPhotoImages
- Pydantic models: CameraDevice, RawDevice, Thumbnail, PhotoImage, PhotoDevice, RequestImagesResult, RequestImagesStatusResult
- `ImageCaptureError` exception for capture timeouts and invalid image data
- 31 new tests for camera models, response envelopes, and client methods

### Changed
- Sequential camera capture (one at a time) to avoid overwhelming panel API
- `capture_image` client method raises `ImageCaptureError` instead of returning None
- Device type mapping uses direct dict lookup (crash on unknown type)
- Non-numeric camera device codes skip with warning instead of silently defaulting to 0
- Camera entities track availability via coordinator state

### Removed
- Periodic capture timer (thumbnails only change after active capture)
- Camera capture interval from options flow (captures are on-demand only)

## 0.3.4 — 2026-04-03

HA integration live on HACS. Published to PyPI as `verisure-italy`.

### Added
- Home Assistant integration (`custom_components/verisure_italy/`)
- AlarmControlPanelEntity: arm home (B), arm away (A), disarm (D)
- Three non-primary states displayed as ARMED_CUSTOM_BYPASS (E, P, T)
- Config flow with 2FA phone picker and installation selection
- DataUpdateCoordinator polling xSStatus every 5s (passive, no timeline spam)
- Force-arm flow: ArmingExceptionError, xSGetExceptions, forceArmingRemoteId
- `verisure_italy.force_arm` / `force_arm_cancel` services
- Persistent notifications for arm failures and open zones
- `verisure_italy_arming_exception` event for automation
- Device info with installation alias and panel model
- Italian shield flag brand icon (HA 2026.3+)
- services.yaml for service definitions
- HACS support (hacs.json)
- MIT license with Verisure non-affiliation disclaimer

### Changed
- Renamed `verisure_api` → `verisure_italy` (import name matches PyPI)
- Renamed `custom_components/verisure_it` → `custom_components/verisure_italy`
- Package published as `verisure-italy` on PyPI
- ARM_PANEL_MUTATION and ARM_STATUS_QUERY updated with forceArmingRemoteId
- `GeneralStatus` model now parses `exceptions` field from xSStatus

### Fixed
- Async service calls (sync `services.call` in async context caused deadlock)
- `APIResponseError` caught in coordinator (no more unavailable blips)
- `PanelError` model: added `suid` field

## 0.2.0 — 2026-04-02

E2E validated API client against live Verisure IT panel.

### Added
- Client integration tests with aioresponses (39 tests mocking HTTP boundary)
- `object` annotation ban in architecture tests (exempts `__eq__`/`__ne__` dunders)
- `PanelError` typed model for arm/disarm error details
- `asyncio.Lock` on token refresh preventing concurrent race condition
- E2E test scripts for manual live API testing (gitignored)

### Fixed
- `Installation.number` needs `Field(alias="numinst")` — real API field name
- `validate_device` returns `hash=null` on success (Verisure IT behavior)
- Poll result fields (numinst, protomResponse, etc.) are null during WAIT state
- `Service.description`, `_OtpResult.msg`, `ValidateResult.msg` can be null
- ERROR poll results now raise `OperationFailedError` instead of `ValueError`
- Explicit None crash on completed operations instead of `or ""` fallbacks

### Removed
- Dead `_try_extract_otp` method (always re-raised, never extracted)
- Dead `graphql_errors` field from `APIResponseError` (always None)
- `dict[str, object]` — replaced with typed `PanelError` model

## 0.1.0 — 2026-04-02

Initial foundation.

### Added
- Project scaffold with CLAUDE.md encoding security-first design tenets
- Six-state alarm model: two-axis (interior OFF/PARTIAL/TOTAL × perimeter OFF/ON)
- Proto response code mapping confirmed from live SDVECU panel (D, E, P, B, T, A)
- Pydantic data models for all API request/response types — zero `Any`
- Typed response envelopes: every GraphQL operation parsed from JSON into Pydantic models
- VerisureClient: async aiohttp client for Verisure Italy GraphQL API
- GraphQL query/mutation definitions (diffable against upstream securitas-direct-new-api)
- Exception hierarchy: one class per failure mode (auth, 2FA, WAF, timeout, etc.)
- AST architecture tests enforcing no `Any`, no bare `dict`, no `object`, no blanket `type: ignore`
- 43 tests passing, pyright strict 0 errors, ruff clean
