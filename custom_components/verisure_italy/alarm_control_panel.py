"""Alarm control panel entity for Verisure Italy."""

from __future__ import annotations

import asyncio
import datetime
import logging

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
)
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pydantic import ValidationError

from verisure_italy import (
    OperationFailedError,
    OperationTimeoutError,
    VerisureError,
)
from verisure_italy.exceptions import ArmingExceptionError
from verisure_italy.models import (
    PROTO_TO_STATE,
    AlarmState,
    InteriorMode,
    PerimeterMode,
    ProtoCode,
)

from . import VerisureConfigEntry
from .const import DOMAIN
from .coordinator import ForceArmContext, VerisureCoordinator

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

# Startup assertion: _STATE_MAP must cover all PROTO_TO_STATE values
assert _STATE_MAP.keys() == set(PROTO_TO_STATE.values()), (
    f"_STATE_MAP keys {set(_STATE_MAP.keys())} don't match "
    f"PROTO_TO_STATE values {set(PROTO_TO_STATE.values())}"
)

_NOTIFICATION_ID_PREFIX = f"{DOMAIN}.arming_exception"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VerisureConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the alarm control panel from a config entry."""
    coordinator = config_entry.runtime_data
    async_add_entities([VerisureAlarmPanel(coordinator)])


class VerisureAlarmPanel(  # type: ignore[reportIncompatibleVariableOverride]
    CoordinatorEntity[VerisureCoordinator], AlarmControlPanelEntity
):
    """Alarm control panel for Verisure Italy."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:shield-home"
    _attr_name = "Alarm"
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
    )
    _attr_code_arm_required = False

    def __init__(self, coordinator: VerisureCoordinator) -> None:
        super().__init__(coordinator)
        inst = coordinator.installation
        self._attr_unique_id = f"{DOMAIN}_{inst.number}_alarm"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, inst.number)},
            name="Verisure",
            manufacturer="Verisure Italy",
            model=f"{inst.panel} — {inst.alias}",
        )
        coordinator.alarm_entity = self
        self._arm_lock = asyncio.Lock()
        self._force_context_timer: CALLBACK_TYPE | None = None
        self._update_alarm_state()

    def _update_alarm_state(self) -> None:
        """Update _attr_alarm_state from coordinator data. Crashes on unknown state."""
        self._attr_alarm_state = _STATE_MAP[self.coordinator.data.alarm_state]
        self._update_force_attributes()

    def _update_force_attributes(self) -> None:
        """Update extra state attributes from force context."""
        ctx = self.coordinator.force_context
        if ctx is not None:
            self._attr_extra_state_attributes = {
                "force_arm_available": True,
                "arm_exceptions": [e.alias for e in ctx.exceptions],
            }
        else:
            self._attr_extra_state_attributes = {}

    def _handle_coordinator_update(self) -> None:
        """Sync alarm state from coordinator."""
        if self._arm_lock.locked():
            # Arm/disarm API call running — state already set explicitly,
            # don't write it again (avoids spurious state_changed events).
            return

        # Always show the real panel state — even during force-arm.
        # The force-arm pending status is communicated through
        # extra_state_attributes, buttons, and notifications.
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
        if self._arm_lock.locked():
            _LOGGER.warning("Arm rejected — another operation in progress")
            return

        async with self._arm_lock:
            _LOGGER.info("Arming %s (target: %s)", mode, target)
            self._attr_alarm_state = AlarmControlPanelState.ARMING
            self.async_write_ha_state()

            try:
                await self.coordinator.async_arm(target)
            except ArmingExceptionError as exc:
                zones = ", ".join(e.alias for e in exc.exceptions)
                _LOGGER.warning(
                    "Arming blocked by %d open zone(s): %s",
                    len(exc.exceptions), zones,
                )
                # Revert to real panel state — the alarm is still disarmed.
                # Force-arm status communicated via attributes + dashboard.
                self._update_alarm_state()
                self._set_force_context(exc, mode, target)
                await self._notify_arm_exceptions(exc)
                self._fire_arming_exception_event(exc, mode)
                self.async_write_ha_state()
                return
            except (OperationFailedError, OperationTimeoutError) as exc:
                self._update_alarm_state()
                _LOGGER.error("Arm failed: %s", exc.message)
                await self._notify_operation_failed("Arm", exc.message)
                self.async_write_ha_state()
                return
            except (VerisureError, ValidationError) as exc:
                self._update_alarm_state()
                msg = exc.message if isinstance(exc, VerisureError) else str(exc)
                _LOGGER.error("Arm failed (unexpected): %s", msg)
                await self._notify_operation_failed("Arm", msg)
                self.async_write_ha_state()
                return

        _LOGGER.info("Armed %s successfully", mode)
        self._expire_force_context()
        await self.coordinator.async_request_refresh()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the alarm."""
        if self._arm_lock.locked():
            _LOGGER.warning("Disarm rejected — another operation in progress")
            return

        async with self._arm_lock:
            _LOGGER.info("Disarming alarm")
            self._attr_alarm_state = AlarmControlPanelState.DISARMING
            self.async_write_ha_state()

            try:
                await self.coordinator.async_disarm()
            except (OperationFailedError, OperationTimeoutError) as exc:
                self._update_alarm_state()
                _LOGGER.error("Disarm failed: %s", exc.message)
                await self._notify_operation_failed("Disarm", exc.message)
                self.async_write_ha_state()
                return
            except (VerisureError, ValidationError) as exc:
                self._update_alarm_state()
                msg = exc.message if isinstance(exc, VerisureError) else str(exc)
                _LOGGER.error("Disarm failed (unexpected): %s", msg)
                await self._notify_operation_failed("Disarm", msg)
                self.async_write_ha_state()
                return

        _LOGGER.info("Disarmed successfully")
        self._expire_force_context()
        await self.coordinator.async_request_refresh()

    # --- Force arm ---

    async def async_force_arm(self) -> None:
        """Force-arm using stored exception context."""
        ctx = self.coordinator.force_context
        if ctx is None:
            _LOGGER.warning("force_arm called but no force context available")
            return

        if self._arm_lock.locked():
            _LOGGER.warning("Force arm rejected — another operation in progress")
            return

        zones = ", ".join(e.alias for e in ctx.exceptions)

        async with self._arm_lock:
            _LOGGER.info(
                "Force-arming with %d bypassed zone(s): %s",
                len(ctx.exceptions), zones,
            )
            await self._dismiss_notification()
            self.async_write_ha_state()

            try:
                await self.coordinator.async_arm(
                    ctx.target,
                    force_arming_remote_id=ctx.reference_id,
                    suid=ctx.suid,
                )
            except (OperationFailedError, OperationTimeoutError) as exc:
                self._clear_force_context()
                self._update_alarm_state()
                _LOGGER.error("Force arm failed: %s", exc.message)
                await self._notify_operation_failed("Force arm", exc.message)
                self.async_write_ha_state()
                return
            except (VerisureError, ValidationError) as exc:
                self._clear_force_context()
                self._update_alarm_state()
                msg = exc.message if isinstance(exc, VerisureError) else str(exc)
                _LOGGER.error("Force arm failed (unexpected): %s", msg)
                await self._notify_operation_failed("Force arm", msg)
                self.async_write_ha_state()
                return

        _LOGGER.info("Force-armed successfully, bypassed: %s", zones)
        self._expire_force_context()
        await self.coordinator.async_request_refresh()

    async def async_force_arm_cancel(self) -> None:
        """Cancel pending force-arm context."""
        if self.coordinator.force_context is None:
            _LOGGER.warning("force_arm_cancel called but no context")
            return

        _LOGGER.info("Force-arm cancelled by user")
        self._clear_force_context()
        self._update_alarm_state()
        await self._dismiss_notification()
        self.async_write_ha_state()

    def _set_force_context(
        self,
        exc: ArmingExceptionError,
        mode: str,
        target: AlarmState,
    ) -> None:
        """Store force-arm context from an arming exception.

        Starts a 120-second timer for deterministic expiry, independent
        of poll interval.
        """
        self._cancel_force_context_timer()
        self.coordinator.force_context = ForceArmContext(
            reference_id=exc.reference_id,
            suid=exc.suid,
            mode=mode,
            target=target,
            exceptions=exc.exceptions,
            created_at=datetime.datetime.now(datetime.UTC),
        )
        self._update_force_attributes()
        self.coordinator.async_update_listeners()

        async def _async_expire_force_context() -> None:
            if self.coordinator.force_context is None:
                return  # already cleared by user action
            _LOGGER.info("Force context expired after 2 minutes")
            self.coordinator.force_context = None
            self._force_context_timer = None
            self._update_force_attributes()
            self._update_alarm_state()
            self.async_write_ha_state()
            self.coordinator.async_update_listeners()
            await self._dismiss_notification()

        def _on_force_context_expired(_now: datetime.datetime) -> None:
            self.hass.async_create_task(
                _async_expire_force_context(),
                "verisure_italy_expire_force_context",
            )

        self._force_context_timer = async_call_later(
            self.hass, 120, _on_force_context_expired
        )

    def _cancel_force_context_timer(self) -> None:
        """Cancel the force context expiry timer if active."""
        if self._force_context_timer is not None:
            self._force_context_timer()
            self._force_context_timer = None

    def _clear_force_context(self) -> None:
        """Clear force context and notify all entities immediately.

        Use in cancel/error paths where we want buttons to disappear NOW.
        """
        if self.coordinator.force_context is None:
            return
        self._cancel_force_context_timer()
        self.coordinator.force_context = None
        self._update_force_attributes()
        self.coordinator.async_update_listeners()

    def _expire_force_context(self) -> None:
        """Clear force context silently — no listener notification.

        Use in success paths where async_request_refresh() will notify
        all entities with fresh data (avoids spurious state transitions
        from stale coordinator data).
        """
        if self.coordinator.force_context is None:
            return
        self._cancel_force_context_timer()
        self.coordinator.force_context = None
        self._update_force_attributes()

    def _notification_id(self) -> str:
        return (
            f"{_NOTIFICATION_ID_PREFIX}"
            f"_{self.coordinator.installation.number}"
        )

    async def _notify_arm_exceptions(self, exc: ArmingExceptionError) -> None:
        """Create a persistent notification about open zones."""
        zone_list = ", ".join(e.alias for e in exc.exceptions)
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "message": f"Arming blocked by open zones: {zone_list}",
                "title": "Verisure Italy — Open Zones",
                "notification_id": self._notification_id(),
            },
        )

    async def _dismiss_notification(self) -> None:
        """Dismiss the arming exception notification."""
        await self.hass.services.async_call(
            "persistent_notification",
            "dismiss",
            {"notification_id": self._notification_id()},
        )

    async def _notify_operation_failed(self, operation: str, message: str) -> None:
        """Create a persistent notification about a failed operation."""
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "message": f"{operation} failed: {message}",
                "title": f"Verisure Italy — {operation} Failed",
                "notification_id": f"{DOMAIN}.operation_failed",
            },
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
