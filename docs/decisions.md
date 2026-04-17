# Design Decisions

Architectural decisions and design tenets for ha-verisure. One entry
per decision. Lead with the decision, then context, then the reasoning.

---

## Fail-secure, not fail-safe

This is security software. Unknown state = ERROR, never "probably
disarmed." Disarm failure = assume still armed. Poll crash = keep
last known state + notify human.

Rationale: a wrong behavior can disarm the alarm. Every design choice
optimizes for correctness over convenience.

---

## Two-axis state model over flat mode enum

Panel state is modeled as `(interior, perimeter)` — interior ∈
`{OFF, PARTIAL, TOTAL}`, perimeter ∈ `{OFF, ON}` — not as a flat enum
like `{disarmed, armed_home, armed_night, armed_away}`.

Rationale: the wire protocol exposes six distinct proto codes
(D, E, P, B, T, A). Collapsing to HA's flat enum would lose
information. Parse the panel's real state, then map to HA at the
display boundary.

See [`architecture-integration.md`](architecture-integration.md).

---

## Parse at the boundary, crash inside

Pydantic models at the HTTP layer. If the Verisure API returns
something we don't model, parsing raises — loudly, at the edge.
Inside the codebase, types guarantee correctness. No `.get()` with
fallbacks on data that must exist.

Rationale: silent fallthrough on unexpected API data can mask a
protocol change that leaves the alarm in an ambiguous state. Crash
loud, surface via HA persistent notification, let the human look.

---

## No HA-level disarm PIN

The integration does not add a second-factor PIN on top of HA's own
auth. Adding one would break automations (auto-disarm) and is
redundant: HA is authenticated, runs in an encrypted VM behind the
home perimeter; the Verisure API itself requires credentials.

Patches welcome for optional PIN support.

---

## Reload integration, not restart HA

For config and code changes, reload the integration via the HA API.
Full `ha core restart` is slow and touches every other integration.

---

## No default arguments on internal functions

Every parameter explicit. `= None` defaults create silent degradation
paths where the caller "just works" with wrong data. The only
acceptable defaults are genuine configuration values (e.g.
`timeout=30`) where the default is the correct production behavior,
not a bypass.
