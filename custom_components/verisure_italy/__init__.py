"""Verisure Italy alarm integration for Home Assistant."""

from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    CONF_INSTALLATION,
    CONF_INSTALLATION_ALIAS,
    CONF_INSTALLATION_NUMBER,
    CONF_INSTALLATION_PANEL,
    CONF_POLL_DELAY,
    CONF_POLL_INTERVAL,
    CONF_POLL_TIMEOUT,
    DEFAULT_POLL_DELAY,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POLL_TIMEOUT,
    DOMAIN,
)
from .coordinator import ForceArmable, VerisureCoordinator

_LOGGER = logging.getLogger(__name__)

type VerisureConfigEntry = ConfigEntry[VerisureCoordinator]

PLATFORMS = ["alarm_control_panel", "button", "camera"]


async def async_migrate_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Migrate config entry between schema versions.

    v1 → v2: synthesize CONF_INSTALLATION from the three legacy scalars
    (number, panel, alias). Metadata fields (name, address, etc.) stay
    None — the soften-on-None semantics in Installation were already the
    right model; v1 was just shoving empty strings into them.
    """
    if entry.version == 1:
        data = {**entry.data}
        data[CONF_INSTALLATION] = {
            "numinst": entry.data[CONF_INSTALLATION_NUMBER],
            "alias": entry.data[CONF_INSTALLATION_ALIAS],
            "panel": entry.data[CONF_INSTALLATION_PANEL],
        }
        hass.config_entries.async_update_entry(entry, data=data, version=2)
        _LOGGER.info(
            "Migrated config entry %s to v2 (numinst=%s)",
            entry.entry_id, entry.data[CONF_INSTALLATION_NUMBER],
        )
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: VerisureConfigEntry
) -> bool:
    """Set up Verisure Italy from a config entry."""
    coordinator = VerisureCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass)

    # Register dashboard panel and populate it
    from .dashboard import async_register_dashboard, async_setup_dashboard

    async_register_dashboard(hass)
    entry.async_create_background_task(
        hass,
        async_setup_dashboard(hass, entry.entry_id),
        "verisure_italy_dashboard_setup",
    )

    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: VerisureConfigEntry
) -> None:
    """Apply options changes without reloading the integration."""
    coordinator = entry.runtime_data
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
    hass: HomeAssistant, entry: VerisureConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    if unload_ok:
        await entry.runtime_data.async_shutdown()

    # Remove services and dashboard if no more entries
    remaining = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if not remaining:
        hass.services.async_remove(DOMAIN, "force_arm")
        hass.services.async_remove(DOMAIN, "force_arm_cancel")
        hass.services.async_remove(DOMAIN, "capture_cameras")

        from .dashboard import async_unregister_dashboard

        async_unregister_dashboard(hass)

    return unload_ok


def _register_services(hass: HomeAssistant) -> None:
    """Register force_arm and force_arm_cancel services."""
    if hass.services.has_service(DOMAIN, "force_arm"):
        return  # Already registered

    def _get_alarm_entity() -> ForceArmable | None:
        """Find the alarm entity via coordinator."""
        for entry in hass.config_entries.async_entries(DOMAIN):
            coordinator: VerisureCoordinator = entry.runtime_data
            if coordinator.alarm_entity is not None:
                return coordinator.alarm_entity
        _LOGGER.error("No VerisureAlarmPanel entity found")
        return None

    async def async_force_arm(call: ServiceCall) -> None:
        """Handle force_arm service call."""
        entity = _get_alarm_entity()
        if entity is not None:
            await entity.async_force_arm()

    async def async_force_arm_cancel(call: ServiceCall) -> None:
        """Handle force_arm_cancel service call."""
        entity = _get_alarm_entity()
        if entity is not None:
            await entity.async_force_arm_cancel()

    hass.services.async_register(
        DOMAIN, "force_arm", async_force_arm, schema=vol.Schema({}),
    )
    hass.services.async_register(
        DOMAIN, "force_arm_cancel", async_force_arm_cancel,
        schema=vol.Schema({}),
    )

    async def async_capture_cameras(call: ServiceCall) -> None:
        """Capture images from all cameras now."""
        for entry in hass.config_entries.async_entries(DOMAIN):
            coordinator: VerisureCoordinator = entry.runtime_data
            await coordinator.async_capture_all_cameras()
            coordinator.notify_camera_entities()

    hass.services.async_register(
        DOMAIN, "capture_cameras", async_capture_cameras,
        schema=vol.Schema({}),
    )
