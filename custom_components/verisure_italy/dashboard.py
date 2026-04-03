"""Auto-managed Lovelace dashboard for Verisure Italy.

Registers a Lovelace panel in the sidebar on integration setup.
The panel is removed when the integration is unloaded.
Dashboard config is rebuilt from discovered entities on every load.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components import frontend
from homeassistant.components.lovelace.dashboard import LovelaceStorage
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

DASHBOARD_URL = "verisure-italy"
DASHBOARD_TITLE = "Verisure"
DASHBOARD_ICON = "mdi:shield-home"
LOVELACE_DATA = "lovelace"


@dataclass
class CameraGroup:
    """A camera device with its associated entities."""

    camera_entity: str
    capture_entity: str | None = None


@dataclass
class DashboardEntities:
    """All entities for the dashboard, grouped by device."""

    alarm_entity: str | None = None
    capture_all_entity: str | None = None
    force_arm_entity: str | None = None
    force_arm_cancel_entity: str | None = None
    cameras: dict[str, CameraGroup] = field(
        default_factory=lambda: dict[str, CameraGroup]()
    )


def async_register_dashboard(hass: HomeAssistant) -> None:
    """Register the Verisure panel in the sidebar."""
    frontend.async_register_built_in_panel(
        hass,
        component_name="lovelace",
        sidebar_title=DASHBOARD_TITLE,
        sidebar_icon=DASHBOARD_ICON,
        frontend_url_path=DASHBOARD_URL,
        config={"mode": "storage"},
        require_admin=False,
        update=True,
    )
    _LOGGER.debug("Registered panel '%s'", DASHBOARD_URL)


def async_unregister_dashboard(hass: HomeAssistant) -> None:
    """Remove the Verisure panel from the sidebar."""
    frontend.async_remove_panel(hass, DASHBOARD_URL)
    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is not None:
        lovelace_data.dashboards.pop(DASHBOARD_URL, None)
    _LOGGER.debug("Unregistered panel '%s'", DASHBOARD_URL)


async def async_setup_dashboard(
    hass: HomeAssistant,
    config_entry_id: str,
) -> None:
    """Create and populate the Verisure dashboard.

    Creates a LovelaceStorage instance for the dashboard if it doesn't
    exist, then writes the card config based on discovered entities.
    """
    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None:
        _LOGGER.debug("Lovelace not loaded, skipping dashboard setup")
        return

    # Create the storage-backed dashboard if it doesn't exist
    lovelace_config = lovelace_data.dashboards.get(DASHBOARD_URL)
    if not isinstance(lovelace_config, LovelaceStorage):
        lovelace_config = LovelaceStorage(hass, {
            "id": DASHBOARD_URL,
            "url_path": DASHBOARD_URL,
            "title": DASHBOARD_TITLE,
            "icon": DASHBOARD_ICON,
            "show_in_sidebar": True,
            "require_admin": False,
        })
        lovelace_data.dashboards[DASHBOARD_URL] = lovelace_config
        _LOGGER.info("Created dashboard storage for '%s'", DASHBOARD_URL)

    # Discover entities and write config
    entities = _discover_entities(hass, config_entry_id)
    if entities.alarm_entity is None:
        return

    config = _build_config(entities)
    await lovelace_config.async_save(config)
    _LOGGER.info(
        "Dashboard updated: alarm + %d cameras", len(entities.cameras)
    )


def _discover_entities(
    hass: HomeAssistant, config_entry_id: str
) -> DashboardEntities:
    """Discover entities grouped by device from the entity registry."""
    registry = er.async_get(hass)
    result = DashboardEntities()

    for entry in registry.entities.get_entries_for_config_entry_id(
        config_entry_id
    ):
        if entry.domain == "alarm_control_panel":
            result.alarm_entity = entry.entity_id
        elif entry.domain == "camera":
            device_id = entry.device_id or ""
            result.cameras.setdefault(
                device_id, CameraGroup(camera_entity=entry.entity_id)
            ).camera_entity = entry.entity_id
        elif entry.domain == "button":
            if "capture_all" in entry.unique_id:
                result.capture_all_entity = entry.entity_id
            elif "force_arm_cancel" in entry.unique_id:
                result.force_arm_cancel_entity = entry.entity_id
            elif "force_arm" in entry.unique_id:
                result.force_arm_entity = entry.entity_id
            else:
                device_id = entry.device_id or ""
                result.cameras.setdefault(
                    device_id, CameraGroup(camera_entity="")
                ).capture_entity = entry.entity_id

    return result


def _build_config(entities: DashboardEntities) -> dict[str, Any]:
    """Build the Lovelace dashboard configuration."""
    assert entities.alarm_entity is not None

    camera_cards: list[dict[str, Any]] = []
    for group in sorted(
        entities.cameras.values(), key=lambda g: g.camera_entity
    ):
        if not group.camera_entity:
            continue

        stack: list[dict[str, Any]] = [
            {
                "type": "picture-entity",
                "entity": group.camera_entity,
                "show_name": True,
                "show_state": False,
                "camera_view": "auto",
            },
        ]
        if group.capture_entity is not None:
            stack.append({
                "type": "tile",
                "entity": group.capture_entity,
                "name": "Capture",
                "icon": "mdi:camera",
                "vertical": False,
                "hide_state": True,
                "tap_action": {"action": "toggle"},
            })

        camera_cards.append({
            "type": "vertical-stack",
            "cards": stack,
        })

    left_cards: list[dict[str, Any]] = [
        {
            "type": "alarm-panel",
            "entity": entities.alarm_entity,
            "name": "Alarm",
            "states": ["arm_home", "arm_away"],
        },
    ]

    # Force-arm buttons — hidden when unavailable (no open zones)
    if entities.force_arm_entity is not None:
        left_cards.append({
            "type": "conditional",
            "conditions": [{
                "condition": "state",
                "entity": entities.force_arm_entity,
                "state_not": "unavailable",
            }],
            "card": {
                "type": "tile",
                "entity": entities.force_arm_entity,
                "name": "Force Arm",
                "icon": "mdi:shield-alert",
                "color": "orange",
                "vertical": False,
                "hide_state": True,
                "tap_action": {"action": "toggle"},
            },
        })
    if entities.force_arm_cancel_entity is not None:
        left_cards.append({
            "type": "conditional",
            "conditions": [{
                "condition": "state",
                "entity": entities.force_arm_cancel_entity,
                "state_not": "unavailable",
            }],
            "card": {
                "type": "tile",
                "entity": entities.force_arm_cancel_entity,
                "name": "Cancel",
                "icon": "mdi:shield-off-outline",
                "color": "red",
                "vertical": False,
                "hide_state": True,
                "tap_action": {"action": "toggle"},
            },
        })

    if entities.capture_all_entity is not None:
        left_cards.append({
            "type": "tile",
            "entity": entities.capture_all_entity,
            "name": "Capture All",
            "icon": "mdi:camera-burst",
            "vertical": False,
            "hide_state": True,
            "tap_action": {"action": "toggle"},
        })

    sections: list[dict[str, Any]] = [
        {
            "type": "grid",
            "column_span": 1,
            "cards": left_cards,
        },
    ]

    if camera_cards:
        sections.append({
            "type": "grid",
            "column_span": 3,
            "cards": camera_cards,
        })

    return {
        "views": [
            {
                "title": DASHBOARD_TITLE,
                "path": "default",
                "icon": DASHBOARD_ICON,
                "type": "sections",
                "max_columns": 4,
                "sections": sections,
            }
        ]
    }
