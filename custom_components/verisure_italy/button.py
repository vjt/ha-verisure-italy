"""Button entities for Verisure Italy — on-demand camera capture."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

if TYPE_CHECKING:
    from verisure_italy import CameraDevice

    from .coordinator import VerisureCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities from config entry."""
    coordinator: VerisureCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    if not coordinator.camera_devices:
        return

    entities: list[ButtonEntity] = [
        VerisureCaptureAllButton(coordinator, config_entry.entry_id),
    ]
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
