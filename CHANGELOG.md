# Changelog

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
- AST architecture tests enforcing no `Any`, no bare `dict`, no blanket `type: ignore`
- 43 tests passing, pyright strict 0 errors, ruff clean
