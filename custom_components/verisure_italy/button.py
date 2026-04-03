"""Button entities for Verisure Italy — camera capture + force-arm."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
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
            VerisureCaptureAllButton(coordinator, config_entry.entry_id),
        )
        entities.extend(
            VerisureCaptureButton(coordinator, camera, config_entry.entry_id)
            for camera in coordinator.camera_devices
        )

    async_add_entities(entities)


class VerisureCaptureAllButton(ButtonEntity):  # type: ignore[reportIncompatibleVariableOverride]
    """Button to capture all cameras at once."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:camera-burst"
    _attr_name = "Capture All Cameras"

    def __init__(
        self,
        coordinator: VerisureCoordinator,
        entry_id: str,
    ) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
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
        return self._coordinator.last_update_success

    async def async_press(self) -> None:
        """Capture all cameras sequentially."""
        from .camera import refresh_all_cameras

        _LOGGER.info("Capture all cameras requested")
        self._capturing = True
        self.async_write_ha_state()
        try:
            await self._coordinator.async_capture_all_cameras()
        finally:
            self._capturing = False
            self.async_write_ha_state()
        refresh_all_cameras(self.hass, self._entry_id)


class VerisureCaptureButton(ButtonEntity):  # type: ignore[reportIncompatibleVariableOverride]
    """Button to trigger an on-demand camera capture."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:camera"

    def __init__(
        self,
        coordinator: VerisureCoordinator,
        camera: CameraDevice,
        entry_id: str,
    ) -> None:
        self._coordinator = coordinator
        self._camera = camera
        self._entry_id = entry_id
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
        return self._coordinator.last_update_success

    async def async_press(self) -> None:
        """Handle button press — capture a fresh image."""
        from .camera import refresh_all_cameras

        _LOGGER.info("Manual capture requested for %s", self._camera.name)
        self._capturing = True
        self.async_write_ha_state()
        try:
            await self._coordinator.capture_single_camera(self._camera)
        finally:
            self._capturing = False
            self.async_write_ha_state()
        refresh_all_cameras(self.hass, self._entry_id)


# --- Force-arm buttons ---


class VerisureForceArmButton(  # type: ignore[reportIncompatibleVariableOverride]
    CoordinatorEntity[VerisureCoordinator], ButtonEntity
):
    """Button to force-arm, bypassing open zones."""

    _attr_has_entity_name = True
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

    @property
    def available(self) -> bool:  # type: ignore[override]
        """Available only when force context is set."""
        if self._pressing:
            return False
        return self.coordinator.force_context is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Show which zones are being bypassed."""
        ctx = self.coordinator.force_context
        if ctx is not None:
            return {
                "open_zones": [e.alias for e in ctx["exceptions"]],
                "mode": ctx["mode"],
            }
        return {}

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
