"""Button entities for Verisure Italy — camera capture + force-arm."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VerisureCoordinator

if TYPE_CHECKING:
    from verisure_italy import CameraDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities from config entry."""
    coordinator: VerisureCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[ButtonEntity] = [
        VerisureForceArmButton(coordinator),
        VerisureForceArmCancelButton(coordinator),
    ]

    if coordinator.camera_devices:
        entities.append(
            VerisureCaptureAllButton(coordinator),
        )
        entities.extend(
            VerisureCaptureButton(coordinator, camera)
            for camera in coordinator.camera_devices
        )

    async_add_entities(entities)


class VerisureCaptureAllButton(  # type: ignore[reportIncompatibleVariableOverride]
    CoordinatorEntity[VerisureCoordinator], ButtonEntity
):
    """Button to capture all cameras at once."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_visible_default = False
    _attr_icon = "mdi:camera-burst"
    _attr_name = "Capture All Cameras"

    def __init__(
        self,
        coordinator: VerisureCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self._capturing = False

        inst = coordinator.installation
        self._attr_unique_id = f"{DOMAIN}_{inst.number}_capture_all"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, inst.number)},
        )

    @property
    def available(self) -> bool:  # type: ignore[override]
        """Unavailable while capture is running (grays out the tile)."""
        if self._capturing:
            return False
        return self.coordinator.last_update_success

    async def async_press(self) -> None:
        """Capture all cameras sequentially."""
        _LOGGER.info("Capture all cameras requested")
        self._capturing = True
        self.async_write_ha_state()
        try:
            await self.coordinator.async_capture_all_cameras()
        finally:
            self._capturing = False
            self.async_write_ha_state()
        self.coordinator.notify_camera_entities()


class VerisureCaptureButton(  # type: ignore[reportIncompatibleVariableOverride]
    CoordinatorEntity[VerisureCoordinator], ButtonEntity
):
    """Button to trigger an on-demand camera capture."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_visible_default = False
    _attr_icon = "mdi:camera"

    def __init__(
        self,
        coordinator: VerisureCoordinator,
        camera: CameraDevice,
    ) -> None:
        super().__init__(coordinator)
        self._camera = camera
        self._capturing = False

        inst = coordinator.installation
        self._attr_unique_id = (
            f"{DOMAIN}_{inst.number}_capture_{camera.zone_id}"
        )
        self._attr_name = "Capture"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{inst.number}_camera_{camera.zone_id}")},
            name=f"Verisure {camera.name}",
            manufacturer="Verisure Italy",
            model=camera.device_type,
            serial_number=camera.serial_number,
            via_device=(DOMAIN, inst.number),
        )

    @property
    def available(self) -> bool:  # type: ignore[override]
        """Unavailable while capture is running (grays out the tile)."""
        if self._capturing:
            return False
        return self.coordinator.last_update_success

    async def async_press(self) -> None:
        """Handle button press — capture a fresh image."""
        _LOGGER.info("Manual capture requested for %s", self._camera.name)
        self._capturing = True
        self.async_write_ha_state()
        try:
            await self.coordinator.capture_single_camera(self._camera)
        finally:
            self._capturing = False
            self.async_write_ha_state()
        self.coordinator.notify_camera_entities()


# --- Force-arm buttons ---


class VerisureForceArmButton(  # type: ignore[reportIncompatibleVariableOverride]
    CoordinatorEntity[VerisureCoordinator], ButtonEntity
):
    """Button to force-arm, bypassing open zones."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_visible_default = False
    _attr_icon = "mdi:shield-alert"
    _attr_name = "Force Arm"

    def __init__(self, coordinator: VerisureCoordinator) -> None:
        super().__init__(coordinator)
        inst = coordinator.installation
        self._attr_unique_id = f"{DOMAIN}_{inst.number}_force_arm"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, inst.number)},
        )
        self._pressing = False
        self._update_force_attributes()

    def _handle_coordinator_update(self) -> None:
        """Update attributes from force context on every coordinator refresh."""
        self._update_force_attributes()
        super()._handle_coordinator_update()

    def _update_force_attributes(self) -> None:
        """Sync extra_state_attributes from current force context."""
        ctx = self.coordinator.force_context
        if ctx is not None:
            self._attr_extra_state_attributes = {
                "open_zones": [e.alias for e in ctx.exceptions],
                "mode": ctx.mode,
            }
        else:
            self._attr_extra_state_attributes = {}

    @property
    def available(self) -> bool:  # type: ignore[override]
        """Available only when force context is set."""
        if self._pressing:
            return False
        return self.coordinator.force_context is not None

    async def async_press(self) -> None:
        """Execute force-arm via the alarm entity."""
        alarm = self.coordinator.alarm_entity
        if alarm is None:
            _LOGGER.error("Force arm pressed but alarm entity not registered")
            return

        _LOGGER.info("Force arm button pressed")
        self._pressing = True
        self.async_write_ha_state()
        try:
            await alarm.async_force_arm()
        finally:
            self._pressing = False
            self.async_write_ha_state()


class VerisureForceArmCancelButton(  # type: ignore[reportIncompatibleVariableOverride]
    CoordinatorEntity[VerisureCoordinator], ButtonEntity
):
    """Button to cancel a pending force-arm."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_visible_default = False
    _attr_icon = "mdi:shield-off-outline"
    _attr_name = "Cancel Force Arm"

    def __init__(self, coordinator: VerisureCoordinator) -> None:
        super().__init__(coordinator)
        inst = coordinator.installation
        self._attr_unique_id = f"{DOMAIN}_{inst.number}_force_arm_cancel"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, inst.number)},
        )

    @property
    def available(self) -> bool:  # type: ignore[override]
        """Available only when force context is set."""
        return self.coordinator.force_context is not None

    async def async_press(self) -> None:
        """Cancel force-arm via the alarm entity."""
        alarm = self.coordinator.alarm_entity
        if alarm is None:
            _LOGGER.error("Cancel pressed but alarm entity not registered")
            return

        _LOGGER.info("Force arm cancel button pressed")
        await alarm.async_force_arm_cancel()
