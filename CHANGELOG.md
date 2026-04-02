# Changelog

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
