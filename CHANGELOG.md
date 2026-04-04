# Changelog

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
