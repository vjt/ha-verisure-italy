"""Camera entities for Verisure Italy."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import VerisureConfigEntry
from .const import (
    DOMAIN,
)
from .coordinator import VerisureCoordinator

if TYPE_CHECKING:
    from verisure_italy import CameraDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VerisureConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up camera entities from config entry."""
    coordinator = config_entry.runtime_data

    if not coordinator.camera_devices:
        _LOGGER.debug("No camera devices found, skipping camera setup")
        return

    entities = [
        VerisureCamera(coordinator, camera)
        for camera in coordinator.camera_devices
    ]
    async_add_entities(entities)
    coordinator.camera_entities = list(entities)

    # Fetch existing cached thumbnails on startup (passive, no panel ping)
    config_entry.async_create_background_task(
        hass,
        coordinator.async_refresh_all_thumbnails(),
        "verisure_italy_thumbnail_refresh",
    )


class VerisureCamera(  # type: ignore[reportIncompatibleVariableOverride]
    CoordinatorEntity[VerisureCoordinator], Camera
):
    """A Verisure Italy camera entity showing the last captured image."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: VerisureCoordinator,
        camera: CameraDevice,
    ) -> None:
        super().__init__(coordinator)
        Camera.__init__(self)
        self._camera = camera
        self._last_image_timestamp: str | None = None

        inst = coordinator.installation
        self._attr_unique_id = f"{DOMAIN}_{inst.number}_camera_{camera.zone_id}"
        self._attr_name = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{inst.number}_camera_{camera.zone_id}")},
            name=f"Verisure {camera.name}",
            manufacturer="Verisure Italy",
            model=camera.device_type,
            serial_number=camera.serial_number,
            via_device=(DOMAIN, inst.number),
        )
        self._update_attrs()

    def _update_attrs(self) -> None:
        """Update extra state attributes from coordinator state."""
        zone = self._camera.zone_id
        attrs: dict[str, str | bool] = {
            "capturing": zone in self.coordinator.camera_capturing,
            "device_type": self._camera.device_type,
            "zone_id": zone,
        }
        timestamp = self.coordinator.camera_timestamps.get(zone)
        if timestamp is not None:
            attrs["image_timestamp"] = timestamp
        self._attr_extra_state_attributes = attrs

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator data update — refresh attrs and detect new images."""
        zone = self._camera.zone_id
        new_ts = self.coordinator.camera_timestamps.get(zone)
        if new_ts != self._last_image_timestamp:
            self._last_image_timestamp = new_ts
            self.async_update_token()
        self._update_attrs()
        super()._handle_coordinator_update()

    # width/height params and their None defaults are mandated by HA's Camera base class
    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the last captured image."""
        return self.coordinator.camera_images.get(self._camera.zone_id)

    def refresh_from_coordinator(self) -> None:
        """Refresh entity state after a capture round."""
        self._update_attrs()
        self.async_update_token()
        self.async_write_ha_state()
