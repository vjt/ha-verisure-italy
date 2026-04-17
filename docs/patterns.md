# Patterns & Gotchas

Repo-specific patterns, HA quirks, and traps that burned us once.
Read before touching similar code.

---

## Never run sync in async context

HA service calls from async handlers must use the async variants
(`hass.services.async_call(...)` with `await`). Using the sync call
from an async context deadlocks the event loop.

---

## Never block the event loop

Pillow, file IO, image encoding — anything CPU-bound or blocking
must run via `hass.async_add_executor_job(...)`. The camera overlay
pass is executor-dispatched for this reason.

---

## Never `yaml.dump` HA's `automations.yaml`

PyYAML mangles Jinja expressions (e.g. `{{ trigger.event.data }}`
becomes quoted strings, breaking templating). Edit `automations.yaml`
by hand or use HA's own UI — never round-trip through PyYAML.

---

## UTC for calculations, local for display

Timestamps used in logic (expiry, backoff, comparisons) stay in UTC.
Only convert to local time at the display boundary (camera overlays,
human-facing strings). Mixing the two caused a force-arm expiry bug.

---

## HA 2026.4+ thread safety in timer callbacks

Callbacks from `async_call_later` must **not** call
`async_write_ha_state` or `async_update_listeners` directly. Dispatch
via `hass.async_create_task(...)` so the state write runs on the
event loop thread.

---

## Entity registry caches attributes

Changing `entity_category`, `entity_registry_visible_default`, etc.
in code does NOT update already-registered entities. Either delete +
re-register, or update via the websocket API
(`config/entity_registry/update`).

---

## `_last_proto` must sync from polls

First arm/disarm after startup would otherwise send an empty string
as the prior proto code. The coordinator populates `_last_proto`
from the first successful poll to avoid this.

---

## Reload, don't restart

`ha core restart` is slow and touches every other integration. Use
the reload-integration endpoint for config/code changes. See
[`decisions.md`](decisions.md).
