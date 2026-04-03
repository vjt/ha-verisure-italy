"""Camera entities for Verisure Italy."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
)

if TYPE_CHECKING:
    from verisure_italy import CameraDevice

    from .coordinator import VerisureCoordinator

_LOGGER = logging.getLogger(__name__)

_CAMERA_ENTITIES: dict[str, list[VerisureCamera]] = {}  # entry_id → entities


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up camera entities from config entry."""
    coordinator: VerisureCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    if not coordinator.camera_devices:
        _LOGGER.debug("No camera devices found, skipping camera setup")
        return

    entities = [
        VerisureCamera(coordinator, camera)
        for camera in coordinator.camera_devices
    ]
    async_add_entities(entities)
    _CAMERA_ENTITIES[config_entry.entry_id] = entities

    # Fetch existing cached thumbnails on startup (passive, no panel ping)
    hass.async_create_task(coordinator.async_refresh_all_thumbnails())


def refresh_all_cameras(hass: HomeAssistant, entry_id: str) -> None:
    """Notify camera entities to refresh state after a capture round."""
    for entity in _CAMERA_ENTITIES.get(entry_id, []):
        entity.refresh_from_coordinator()


class VerisureCamera(Camera):  # type: ignore[reportIncompatibleVariableOverride]
    """A Verisure Italy camera entity showing the last captured image."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: VerisureCoordinator,
        camera: CameraDevice,
    ) -> None:
        super().__init__()
        self._coordinator = coordinator
        self._camera = camera

        inst = coordinator.installation
        self._attr_unique_id = f"{DOMAIN}_{inst.number}_camera_{camera.zone_id}"
        self._attr_name = "Camera"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{inst.number}_camera_{camera.zone_id}")},
            name=camera.name,
            manufacturer="Verisure Italy",
            model=camera.device_type,
            serial_number=camera.serial_number,
            via_device=(DOMAIN, inst.number),
        )
        self._update_attrs()

    @property
    def available(self) -> bool:
        """Camera is available when the coordinator's last update succeeded."""
        return self._coordinator.last_update_success

    def _update_attrs(self) -> None:
        """Update extra state attributes from coordinator state."""
        zone = self._camera.zone_id
        attrs: dict[str, str | bool] = {
            "capturing": zone in self._coordinator.camera_capturing,
            "device_type": self._camera.device_type,
            "zone_id": zone,
        }
        timestamp = self._coordinator.camera_timestamps.get(zone)
        if timestamp is not None:
            attrs["image_timestamp"] = timestamp
        self._attr_extra_state_attributes = attrs

    # width/height params and their None defaults are mandated by HA's Camera base class
    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the last captured image."""
        return self._coordinator.camera_images.get(self._camera.zone_id)

    def refresh_from_coordinator(self) -> None:
        """Refresh entity state after a capture round."""
        self._update_attrs()
        self.async_update_token()
        self.async_write_ha_state()
