"""Verisure Italy alarm integration for Home Assistant."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN
from .coordinator import VerisureCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["alarm_control_panel"]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Set up Verisure Italy from a config entry."""
    coordinator = VerisureCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass)

    return True


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
