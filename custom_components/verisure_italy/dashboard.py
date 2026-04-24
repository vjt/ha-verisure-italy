"""Auto-managed Lovelace dashboard for Verisure Italy.

Registers a Lovelace panel in the sidebar on integration setup.
The panel is removed when the integration is unloaded.
Dashboard config is rebuilt from discovered entities on every load.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, TypedDict, cast

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pydantic import BaseModel, ConfigDict, Field

_LOGGER = logging.getLogger(__name__)

DASHBOARD_URL = "verisure-italy"
DASHBOARD_TITLE = "Verisure"
DASHBOARD_ICON = "mdi:shield-home"
LOVELACE_DATA = "lovelace"


# --- Entity discovery result types (Pydantic, per CLAUDE.md) ---


class CameraGroup(BaseModel):
    """A camera device with its associated entities."""

    model_config = ConfigDict(frozen=False)

    camera_entity: str | None = None
    capture_entity: str | None = None


class DashboardEntities(BaseModel):
    """All entities for the dashboard, grouped by device."""

    model_config = ConfigDict(frozen=False)

    alarm_entity: str | None = None
    capture_all_entity: str | None = None
    force_arm_entity: str | None = None
    force_arm_cancel_entity: str | None = None
    cameras: dict[str, CameraGroup] = Field(default_factory=dict)


# --- Lovelace card TypedDicts (narrow, covers the shapes we emit) ---
#
# These do NOT replace HA's Lovelace schema — they just make typos in
# keys ("entiy" vs "entity") a pyright error instead of a silent bug.


class _TapAction(TypedDict):
    action: Literal["toggle", "more-info", "navigate", "none"]


class _StateCondition(TypedDict, total=False):
    condition: Literal["state"]
    entity: str
    state: str
    state_not: str


class PictureEntityCard(TypedDict):
    type: Literal["picture-entity"]
    entity: str
    show_name: bool
    show_state: bool
    camera_view: Literal["auto", "live"]


class TileCard(TypedDict, total=False):
    type: Literal["tile"]
    entity: str
    name: str
    icon: str
    color: str
    vertical: bool
    hide_state: bool
    tap_action: _TapAction


class MarkdownCard(TypedDict):
    type: Literal["markdown"]
    content: str


class AlarmPanelCard(TypedDict):
    type: Literal["alarm-panel"]
    entity: str
    name: str
    states: list[str]


class VerticalStackCard(TypedDict):
    type: Literal["vertical-stack"]
    cards: list[LovelaceCard]


class ConditionalCard(TypedDict):
    type: Literal["conditional"]
    conditions: list[_StateCondition]
    card: LovelaceCard


class GridSection(TypedDict):
    type: Literal["grid"]
    column_span: int
    cards: list[LovelaceCard]


# Union of all card shapes we emit.
LovelaceCard = (
    PictureEntityCard
    | TileCard
    | MarkdownCard
    | AlarmPanelCard
    | VerticalStackCard
    | ConditionalCard
    | GridSection
)


class SectionsView(TypedDict):
    title: str
    path: str
    icon: str
    type: Literal["sections"]
    max_columns: int
    sections: list[GridSection]


class LovelaceConfig(TypedDict):
    views: list[SectionsView]


# --- Panel register / unregister ---


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
    try:
        frontend.async_remove_panel(hass, DASHBOARD_URL)
        lovelace_data = hass.data.get(LOVELACE_DATA)
        if lovelace_data is not None:
            lovelace_data.dashboards.pop(DASHBOARD_URL, None)
        _LOGGER.debug("Unregistered panel '%s'", DASHBOARD_URL)
    except Exception:
        _LOGGER.exception("Dashboard cleanup failed — ignoring")


async def async_setup_dashboard(
    hass: HomeAssistant,
    config_entry_id: str,
) -> None:
    """Create and populate the Verisure dashboard.

    Uses HA Lovelace internals (LovelaceStorage) — these are NOT public API
    and may break on HA updates. Wrapped in try/except so dashboard failure
    never prevents the integration from loading.
    """
    try:
        await _setup_dashboard_internal(hass, config_entry_id)
    except Exception:
        _LOGGER.exception(
            "Dashboard setup failed — Lovelace internals may have changed. "
            "The integration works fine without the dashboard. "
            "Report this at https://github.com/vjt/ha-verisure-italy/issues"
        )
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "message": (
                    "Verisure dashboard setup failed — likely due to a Home Assistant "
                    "update changing Lovelace internals. The alarm and cameras work "
                    "normally. Check logs for details."
                ),
                "title": "Verisure Italy — Dashboard Error",
                "notification_id": "verisure_italy.dashboard_error",
            },
        )


async def _setup_dashboard_internal(
    hass: HomeAssistant,
    config_entry_id: str,
) -> None:
    """Internal dashboard setup — may raise on HA Lovelace changes."""
    from homeassistant.components.lovelace.dashboard import LovelaceStorage

    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None:
        _LOGGER.debug("Lovelace not loaded, skipping dashboard setup")
        return

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

    entities = _discover_entities(hass, config_entry_id)
    if entities.alarm_entity is None:
        return

    config = _build_config(entities)
    # HA Lovelace storage accepts a plain dict; our TypedDict is a
    # compile-time shape, so cast at the boundary.
    await lovelace_config.async_save(cast("dict[str, Any]", config))
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
            uid = entry.unique_id or ""
            if uid.endswith("_capture_all"):
                result.capture_all_entity = entry.entity_id
            elif uid.endswith("_force_arm_cancel"):
                result.force_arm_cancel_entity = entry.entity_id
            elif uid.endswith("_force_arm"):
                result.force_arm_entity = entry.entity_id
            elif "_capture_" in uid:
                device_id = entry.device_id or ""
                result.cameras.setdefault(
                    device_id, CameraGroup()
                ).capture_entity = entry.entity_id

    return result


def _build_config(entities: DashboardEntities) -> LovelaceConfig:
    """Build the Lovelace dashboard configuration."""
    assert entities.alarm_entity is not None

    camera_cards: list[LovelaceCard] = []
    for group in sorted(
        entities.cameras.values(), key=lambda g: g.camera_entity or ""
    ):
        if group.camera_entity is None:
            continue

        stack: list[LovelaceCard] = [
            PictureEntityCard(
                type="picture-entity",
                entity=group.camera_entity,
                show_name=True,
                show_state=False,
                camera_view="auto",
            ),
        ]
        if group.capture_entity is not None:
            stack.append(TileCard(
                type="tile",
                entity=group.capture_entity,
                name="Capture",
                icon="mdi:camera",
                vertical=False,
                hide_state=True,
                tap_action=_TapAction(action="toggle"),
            ))

        camera_cards.append(VerticalStackCard(
            type="vertical-stack",
            cards=stack,
        ))

    left_cards: list[LovelaceCard] = []

    # Alert banner — appears only when arming was blocked by open zones
    if entities.force_arm_entity is not None:
        left_cards.append(ConditionalCard(
            type="conditional",
            conditions=[{
                "condition": "state",
                "entity": entities.force_arm_entity,
                "state_not": "unavailable",
            }],
            card=MarkdownCard(
                type="markdown",
                content=(
                    "## ⚠️ Arming blocked\n"
                    "Open zones detected. **Force Arm** to bypass or **Cancel**."
                ),
            ),
        ))

    left_cards.append(AlarmPanelCard(
        type="alarm-panel",
        entity=entities.alarm_entity,
        name="Alarm",
        states=["arm_home", "arm_away"],
    ))

    # Force-arm buttons — hidden when unavailable (no open zones)
    if entities.force_arm_entity is not None:
        left_cards.append(ConditionalCard(
            type="conditional",
            conditions=[{
                "condition": "state",
                "entity": entities.force_arm_entity,
                "state_not": "unavailable",
            }],
            card=TileCard(
                type="tile",
                entity=entities.force_arm_entity,
                name="Force Arm",
                icon="mdi:shield-alert",
                color="orange",
                vertical=False,
                hide_state=True,
                tap_action=_TapAction(action="toggle"),
            ),
        ))
    if entities.force_arm_cancel_entity is not None:
        left_cards.append(ConditionalCard(
            type="conditional",
            conditions=[{
                "condition": "state",
                "entity": entities.force_arm_cancel_entity,
                "state_not": "unavailable",
            }],
            card=TileCard(
                type="tile",
                entity=entities.force_arm_cancel_entity,
                name="Cancel",
                icon="mdi:shield-off-outline",
                color="red",
                vertical=False,
                hide_state=True,
                tap_action=_TapAction(action="toggle"),
            ),
        ))

    if entities.capture_all_entity is not None:
        left_cards.append(TileCard(
            type="tile",
            entity=entities.capture_all_entity,
            name="Capture All",
            icon="mdi:camera-burst",
            vertical=False,
            hide_state=True,
            tap_action=_TapAction(action="toggle"),
        ))

    sections: list[GridSection] = [
        GridSection(
            type="grid",
            column_span=1,
            cards=left_cards,
        ),
    ]

    if camera_cards:
        sections.append(GridSection(
            type="grid",
            column_span=3,
            cards=camera_cards,
        ))

    return LovelaceConfig(
        views=[
            SectionsView(
                title=DASHBOARD_TITLE,
                path="default",
                icon=DASHBOARD_ICON,
                type="sections",
                max_columns=4,
                sections=sections,
            )
        ]
    )
