"""Verisure Italy alarm integration for Home Assistant."""

from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    CONF_POLL_DELAY,
    CONF_POLL_INTERVAL,
    CONF_POLL_TIMEOUT,
    DEFAULT_POLL_DELAY,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POLL_TIMEOUT,
    DOMAIN,
)
from .coordinator import VerisureCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["alarm_control_panel", "button", "camera"]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Set up Verisure Italy from a config entry."""
    coordinator = VerisureCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass)

    # Create/update dashboard after entities are registered
    from .dashboard import async_setup_dashboard

    hass.async_create_task(
        async_setup_dashboard(hass, entry.entry_id)
    )

    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Apply options changes without reloading the integration."""
    coordinator: VerisureCoordinator = hass.data[DOMAIN][entry.entry_id]
    opts = entry.options

    coordinator.update_interval = timedelta(
        seconds=opts.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
    )
    coordinator.client.set_poll_params(
        timeout=float(opts.get(CONF_POLL_TIMEOUT, DEFAULT_POLL_TIMEOUT)),
        delay=float(opts.get(CONF_POLL_DELAY, DEFAULT_POLL_DELAY)),
    )
    _LOGGER.info(
        "Options updated: interval=%ss, timeout=%ss, delay=%ss",
        opts.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        opts.get(CONF_POLL_TIMEOUT, DEFAULT_POLL_TIMEOUT),
        opts.get(CONF_POLL_DELAY, DEFAULT_POLL_DELAY),
    )


async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    if unload_ok:
        coordinator: VerisureCoordinator = hass.data[DOMAIN].pop(
            entry.entry_id
        )
        await coordinator.async_shutdown()

    # Remove services if no more entries
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, "force_arm")
        hass.services.async_remove(DOMAIN, "force_arm_cancel")
        hass.services.async_remove(DOMAIN, "capture_cameras")
        hass.data.pop(DOMAIN, None)

    return unload_ok


def _register_services(hass: HomeAssistant) -> None:
    """Register force_arm and force_arm_cancel services."""
    if hass.services.has_service(DOMAIN, "force_arm"):
        return  # Already registered

    async def _get_entity(call: ServiceCall):
        """Find the VerisureAlarmPanel entity from a service call."""
        from .alarm_control_panel import VerisureAlarmPanel

        entity_id = call.data["entity_id"]
        component = hass.data.get("entity_components", {}).get(
            "alarm_control_panel"
        )
        if component is not None:
            entity = component.get_entity(entity_id)
            if isinstance(entity, VerisureAlarmPanel):
                return entity

        _LOGGER.error("Could not find VerisureAlarmPanel for %s", entity_id)
        return None

    async def async_force_arm(call: ServiceCall) -> None:
        """Handle force_arm service call."""
        entity = await _get_entity(call)
        if entity is not None:
            await entity.async_force_arm()

    async def async_force_arm_cancel(call: ServiceCall) -> None:
        """Handle force_arm_cancel service call."""
        entity = await _get_entity(call)
        if entity is not None:
            await entity.async_force_arm_cancel()

    service_schema = vol.Schema({
        vol.Required("entity_id"): str,
    })

    hass.services.async_register(
        DOMAIN, "force_arm", async_force_arm, schema=service_schema
    )
    hass.services.async_register(
        DOMAIN, "force_arm_cancel", async_force_arm_cancel,
        schema=service_schema,
    )

    async def async_capture_cameras(call: ServiceCall) -> None:
        """Capture images from all cameras now."""
        for entry_id, coordinator in hass.data[DOMAIN].items():
            if isinstance(coordinator, VerisureCoordinator):
                await coordinator.async_capture_all_cameras()
                # Notify camera entities to refresh
                from .camera import refresh_all_cameras

                refresh_all_cameras(hass, entry_id)

    hass.services.async_register(
        DOMAIN, "capture_cameras", async_capture_cameras,
    )
