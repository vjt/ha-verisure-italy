"""Lovelace dashboard for Verisure Italy.

Auto-updates the 'verisure-italy' dashboard config on integration setup.
The dashboard itself must be created once — either from the HA UI
(Settings → Dashboards → Add, URL path: verisure-italy) or via the
setup_dashboard.py script.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.lovelace.dashboard import LovelaceStorage
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

DASHBOARD_URL = "verisure-italy"


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
    cameras: dict[str, CameraGroup] = field(
        default_factory=lambda: dict[str, CameraGroup]()
    )


async def async_setup_dashboard(
    hass: HomeAssistant,
    config_entry_id: str,
) -> None:
    """Update the Verisure dashboard if it exists."""
    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None:
        return

    lovelace_config = lovelace_data.dashboards.get(DASHBOARD_URL)
    if not isinstance(lovelace_config, LovelaceStorage):
        _LOGGER.info(
            "No '%s' dashboard found. Create one from Settings → Dashboards "
            "→ Add Dashboard (URL path: %s) and reload the integration",
            DASHBOARD_URL,
            DASHBOARD_URL,
        )
        return

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
                "title": "Verisure",
                "path": "default",
                "icon": "mdi:shield-home",
                "type": "sections",
                "max_columns": 4,
                "sections": sections,
            }
        ]
    }
