"""Alarm control panel entity for Verisure Italy."""

from __future__ import annotations

import datetime
import logging
from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
)
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from verisure_italy import (
    OperationFailedError,
    OperationTimeoutError,
)
from verisure_italy.exceptions import ArmingExceptionError
from verisure_italy.models import (
    PROTO_TO_STATE,
    AlarmState,
    InteriorMode,
    PerimeterMode,
    ProtoCode,
)

from .const import DOMAIN
from .coordinator import VerisureCoordinator

_LOGGER = logging.getLogger(__name__)

# Canonical target states for arm actions
_PARTIAL_PERIMETER = AlarmState(
    interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON
)
_TOTAL_PERIMETER = AlarmState(
    interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON
)
_DISARMED = AlarmState(
    interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF
)

# Map our AlarmState to HA AlarmControlPanelState
_STATE_MAP: dict[AlarmState, AlarmControlPanelState] = {
    _DISARMED: AlarmControlPanelState.DISARMED,
    _PARTIAL_PERIMETER: AlarmControlPanelState.ARMED_HOME,
    _TOTAL_PERIMETER: AlarmControlPanelState.ARMED_AWAY,
    PROTO_TO_STATE[ProtoCode.PERIMETER_ONLY]: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
    PROTO_TO_STATE[ProtoCode.PARTIAL]: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
    PROTO_TO_STATE[ProtoCode.TOTAL]: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
}

_NOTIFICATION_ID_PREFIX = f"{DOMAIN}.arming_exception"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the alarm control panel from a config entry."""
    coordinator: VerisureCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([VerisureAlarmPanel(coordinator)])


class VerisureAlarmPanel(  # type: ignore[reportIncompatibleVariableOverride]
    CoordinatorEntity[VerisureCoordinator], AlarmControlPanelEntity
):
    """Alarm control panel for Verisure Italy."""

    _attr_has_entity_name = True
    _attr_name = None  # Use device name
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
    )
    _attr_code_arm_required = False

    def __init__(self, coordinator: VerisureCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.installation.number}"
        )
        self._force_context: dict[str, Any] | None = None
        self._update_alarm_state()

    def _update_alarm_state(self) -> None:
        """Update _attr_alarm_state from coordinator data."""
        self._attr_alarm_state = _STATE_MAP.get(
            self.coordinator.data.alarm_state
        )
        self._update_force_attributes()

    def _update_force_attributes(self) -> None:
        """Update extra state attributes from force context."""
        if self._force_context is not None:
            self._attr_extra_state_attributes = {
                "force_arm_available": True,
                "arm_exceptions": [
                    e.alias for e in self._force_context["exceptions"]
                ],
            }
        else:
            self._attr_extra_state_attributes = {}

    def _handle_coordinator_update(self) -> None:
        """Sync alarm state from coordinator and clear force context."""
        self._force_context = None
        self._update_alarm_state()
        super()._handle_coordinator_update()

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm partial + perimeter."""
        await self._async_arm(_PARTIAL_PERIMETER, "armed_home")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm total + perimeter."""
        await self._async_arm(_TOTAL_PERIMETER, "armed_away")

    async def _async_arm(self, target: AlarmState, mode: str) -> None:
        """Execute arm operation with force-arm exception handling."""
        self._attr_alarm_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()

        try:
            await self.coordinator.client.arm(
                self.coordinator.installation, target
            )
        except ArmingExceptionError as exc:
            self._update_alarm_state()  # revert to previous
            self._set_force_context(exc, mode, target)
            self._notify_arm_exceptions(exc)
            self._fire_arming_exception_event(exc, mode)
            self.async_write_ha_state()
            return
        except (OperationFailedError, OperationTimeoutError) as exc:
            self._update_alarm_state()  # revert to previous
            _LOGGER.error("Arm failed: %s", exc.message)
            self.async_write_ha_state()
            return

        await self.coordinator.async_request_refresh()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the alarm."""
        self._attr_alarm_state = AlarmControlPanelState.DISARMING
        self.async_write_ha_state()

        try:
            await self.coordinator.client.disarm(
                self.coordinator.installation
            )
        except (OperationFailedError, OperationTimeoutError) as exc:
            self._update_alarm_state()  # revert to previous
            _LOGGER.error("Disarm failed: %s", exc.message)
            self.async_write_ha_state()
            return

        await self.coordinator.async_request_refresh()

    # --- Force arm ---

    async def async_force_arm(self) -> None:
        """Force-arm using stored exception context."""
        if self._force_context is None:
            _LOGGER.warning("force_arm called but no force context available")
            return

        target: AlarmState = self._force_context["target"]
        ref_id: str = self._force_context["reference_id"]

        self._force_context = None
        self._attr_alarm_state = AlarmControlPanelState.ARMING
        self._update_force_attributes()
        self._dismiss_notification()
        self.async_write_ha_state()

        try:
            await self.coordinator.client.arm(
                self.coordinator.installation,
                target,
                force_arming_remote_id=ref_id,
            )
        except (OperationFailedError, OperationTimeoutError) as exc:
            self._update_alarm_state()
            _LOGGER.error("Force arm failed: %s", exc.message)
            self.async_write_ha_state()
            return

        await self.coordinator.async_request_refresh()

    async def async_force_arm_cancel(self) -> None:
        """Cancel pending force-arm context."""
        if self._force_context is None:
            _LOGGER.warning("force_arm_cancel called but no context")
            return

        _LOGGER.info("Force-arm cancelled by user")
        self._force_context = None
        self._update_force_attributes()
        self._dismiss_notification()
        self.async_write_ha_state()

    def _set_force_context(
        self,
        exc: ArmingExceptionError,
        mode: str,
        target: AlarmState,
    ) -> None:
        """Store force-arm context from an arming exception."""
        self._force_context = {
            "reference_id": exc.reference_id,
            "suid": exc.suid,
            "mode": mode,
            "target": target,
            "exceptions": exc.exceptions,
            "created_at": datetime.datetime.now(),
        }
        self._update_force_attributes()

    def _notification_id(self) -> str:
        return (
            f"{_NOTIFICATION_ID_PREFIX}"
            f"_{self.coordinator.installation.number}"
        )

    def _notify_arm_exceptions(self, exc: ArmingExceptionError) -> None:
        """Create a persistent notification about open zones."""
        zone_list = ", ".join(e.alias for e in exc.exceptions)
        self.hass.services.call(
            "persistent_notification",
            "create",
            {
                "message": f"Arming blocked by open zones: {zone_list}",
                "title": "Verisure Italy — Open Zones",
                "notification_id": self._notification_id(),
            },
        )

    def _dismiss_notification(self) -> None:
        """Dismiss the arming exception notification."""
        self.hass.services.call(
            "persistent_notification",
            "dismiss",
            {"notification_id": self._notification_id()},
        )

    def _fire_arming_exception_event(
        self, exc: ArmingExceptionError, mode: str
    ) -> None:
        """Fire an HA event for automation consumption."""
        self.hass.bus.async_fire(
            f"{DOMAIN}_arming_exception",
            {
                "entity_id": self.entity_id,
                "zones": [e.alias for e in exc.exceptions],
                "mode": mode,
            },
        )
