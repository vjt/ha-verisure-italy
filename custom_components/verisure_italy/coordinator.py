"""DataUpdateCoordinator for Verisure Italy."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from aiohttp import ClientSession
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from verisure_italy import (
    AlarmState,
    AuthenticationError,
    CameraDevice,
    GeneralStatus,
    Installation,
    ProtoCode,
    SessionExpiredError,
    VerisureClient,
    WAFBlockedError,
    parse_proto_code,
)
from verisure_italy.exceptions import (
    APIConnectionError,
    APIResponseError,
    ImageCaptureError,
    OperationFailedError,
    OperationTimeoutError,
    UnexpectedStateError,
)
from verisure_italy.models import PROTO_TO_STATE, ZoneException

from .const import (
    CONF_DEVICE_ID,
    CONF_INSTALLATION_ALIAS,
    CONF_INSTALLATION_NUMBER,
    CONF_INSTALLATION_PANEL,
    CONF_POLL_DELAY,
    CONF_POLL_INTERVAL,
    CONF_POLL_TIMEOUT,
    CONF_UUID,
    DEFAULT_POLL_DELAY,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POLL_TIMEOUT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _overlay_text(jpeg_bytes: bytes, camera_name: str, timestamp: datetime) -> bytes:
    """Overlay camera name and timestamp on a JPEG image.

    Draws white text with dark shadow in the bottom-left corner.
    Returns the modified JPEG as bytes.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(io.BytesIO(jpeg_bytes))
    draw = ImageDraw.Draw(img)

    # Use default font — no external font files needed
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    text = f"{camera_name}  {timestamp.strftime('%Y-%m-%d %H:%M:%S')}"

    # Position: bottom-left with padding
    x = 8
    y = img.height - 24

    # Dark shadow for readability
    draw.text((x + 1, y + 1), text, fill="black", font=font)
    draw.text((x, y), text, fill="white", font=font)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


@dataclass(frozen=True)
class VerisureStatusData:
    """Data returned by the coordinator."""

    alarm_state: AlarmState
    proto_code: ProtoCode
    timestamp: str
    exceptions: list[ZoneException]


class VerisureCoordinator(DataUpdateCoordinator[VerisureStatusData]):
    """Coordinator that polls xSStatus for passive alarm state."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        opts = config_entry.options
        poll_interval = opts.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        poll_timeout = opts.get(CONF_POLL_TIMEOUT, DEFAULT_POLL_TIMEOUT)
        poll_delay = opts.get(CONF_POLL_DELAY, DEFAULT_POLL_DELAY)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
            config_entry=config_entry,
        )

        self._session = ClientSession()
        self.client = VerisureClient(
            username=config_entry.data[CONF_USERNAME],
            password=config_entry.data[CONF_PASSWORD],
            http_session=self._session,
            device_id=config_entry.data[CONF_DEVICE_ID],
            uuid=config_entry.data[CONF_UUID],
            id_device_indigitall="",
            poll_timeout=float(poll_timeout),
            poll_delay=float(poll_delay),
        )
        self.installation = Installation(
            numinst=config_entry.data[CONF_INSTALLATION_NUMBER],
            alias=config_entry.data[CONF_INSTALLATION_ALIAS],
            panel=config_entry.data[CONF_INSTALLATION_PANEL],
            type="",
            name="",
            surname="",
            address="",
            city="",
            postcode="",
            province="",
            email="",
            phone="",
        )

        # Camera state — populated during first refresh
        self.camera_devices: list[CameraDevice] = []
        self._cameras_discovered = False
        self.camera_images: dict[str, bytes] = {}  # zone_id → JPEG bytes
        self.camera_timestamps: dict[str, str] = {}  # zone_id → timestamp
        self.camera_capturing: set[str] = set()  # zone_ids currently capturing
        self._capture_lock = asyncio.Lock()  # one capture at a time

        # Force-arm context — shared between alarm entity and force-arm buttons
        self.force_context: dict[str, Any] | None = None

        # Set by VerisureAlarmPanel.__init__ — used by force-arm buttons
        self.alarm_entity: Any = None

    async def async_shutdown(self) -> None:
        """Close the HTTP session."""
        await super().async_shutdown()
        await self._session.close()

    async def _async_update_data(self) -> VerisureStatusData:
        """Poll xSStatus for current alarm state."""
        try:
            status: GeneralStatus = await self.client.get_general_status(
                self.installation
            )
        except SessionExpiredError:
            _LOGGER.debug("Session expired, re-authenticating")
            try:
                await self.client.login()
                status = await self.client.get_general_status(
                    self.installation
                )
            except AuthenticationError as err:
                raise ConfigEntryAuthFailed(
                    f"Re-authentication failed: {err.message}"
                ) from err
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(err.message) from err
        except (APIConnectionError, APIResponseError, WAFBlockedError) as err:
            raise UpdateFailed(err.message) from err
        except UnexpectedStateError as err:
            _LOGGER.error("Unexpected alarm state: %s", err.proto_code)
            raise UpdateFailed(err.message) from err

        # Discover camera devices on first successful refresh
        if not self._cameras_discovered:
            self._cameras_discovered = True
            try:
                self.camera_devices = await self.client.list_camera_devices(
                    self.installation
                )
                _LOGGER.info(
                    "Discovered %d cameras: %s",
                    len(self.camera_devices),
                    ", ".join(c.name for c in self.camera_devices),
                )
            except (APIConnectionError, APIResponseError, WAFBlockedError) as err:
                _LOGGER.warning("Camera discovery failed: %s", err.message)
                self.camera_devices = []

        proto = parse_proto_code(status.status)
        alarm_state = PROTO_TO_STATE[proto]

        return VerisureStatusData(
            alarm_state=alarm_state,
            proto_code=proto,
            timestamp=status.timestamp_update,
            exceptions=status.exceptions or [],
        )

    async def async_capture_all_cameras(self) -> None:
        """Capture all cameras concurrently, staggered 0.5s apart.

        Uses a lock so only one capture round runs at a time.
        Cameras launch 0.5s apart to be polite to the API, but all
        run concurrently. Individual failures retry with backoff.
        """
        if not self.camera_devices:
            return

        if self._capture_lock.locked():
            _LOGGER.debug("Capture round already in progress, skipping")
            return

        async with self._capture_lock:
            _LOGGER.info(
                "Starting capture for %d cameras (2s stagger)",
                len(self.camera_devices),
            )

            async def _launch(camera: CameraDevice, delay: float) -> bool:
                if delay > 0:
                    await asyncio.sleep(delay)
                return await self.capture_single_camera(camera)

            results = await asyncio.gather(*(
                _launch(camera, i * 2.0)
                for i, camera in enumerate(self.camera_devices)
            ), return_exceptions=True)

            ok = sum(1 for r in results if r is True)
            fail = len(results) - ok
            _LOGGER.info("Capture round complete: %d ok, %d failed", ok, fail)

    async def async_refresh_all_thumbnails(self) -> None:
        """Passively refresh thumbnails from Verisure CDN. No panel ping.

        This is the camera equivalent of xSStatus — reads server-side cached
        images without creating timeline entries or app notifications.
        """
        if not self.camera_devices:
            return

        if self._capture_lock.locked():
            _LOGGER.debug("Active capture in progress, skipping thumbnail refresh")
            return

        async with self._capture_lock:
            for camera in self.camera_devices:
                await self._fetch_thumbnail(camera)

    async def capture_single_camera(
        self, camera: CameraDevice, _retries_left: int = 2
    ) -> bool:
        """Capture a single camera image. Returns True on success.

        Uses capture_image for the thumbnail, then tries get_photo_images
        for a full-resolution version. Stores whichever is best.
        Retries with exponential backoff on failure.
        """
        self.camera_capturing.add(camera.zone_id)
        try:
            image_bytes = await self.client.capture_image(
                self.installation, camera
            )
        except (
            APIConnectionError,
            APIResponseError,
            WAFBlockedError,
            SessionExpiredError,
            OperationFailedError,
            OperationTimeoutError,
            ImageCaptureError,
        ) as err:
            self.camera_capturing.discard(camera.zone_id)
            if _retries_left > 0:
                backoff = 3 * (3 - _retries_left)  # 3s, 6s
                _LOGGER.info(
                    "Capture failed for %s: %s — retrying in %ds (%d left)",
                    camera.name, err.message, backoff, _retries_left,
                )
                await asyncio.sleep(backoff)
                return await self.capture_single_camera(
                    camera, _retries_left=_retries_left - 1
                )
            _LOGGER.warning(
                "Capture failed for %s: %s (no more retries)",
                camera.name, err.message,
            )
            return False

        now = datetime.now()
        self.camera_timestamps[camera.zone_id] = now.isoformat()
        self.camera_images[camera.zone_id] = await self.hass.async_add_executor_job(
            _overlay_text, image_bytes, camera.name, now
        )
        _LOGGER.info(
            "Captured %s: %d bytes", camera.name, len(image_bytes)
        )

        # Try to upgrade to full-resolution image (some panels have higher-res)
        await self._try_full_image(camera, now)

        self.camera_capturing.discard(camera.zone_id)
        return True

    async def _try_full_image(
        self, camera: CameraDevice, timestamp: datetime
    ) -> None:
        """Try to fetch full-res image using the latest thumbnail's id_signal."""
        try:
            thumbnail = await self.client.get_thumbnail(
                self.installation, camera
            )
        except (
            APIConnectionError, APIResponseError,
            WAFBlockedError, SessionExpiredError,
        ):
            return

        if not thumbnail.id_signal or not thumbnail.signal_type:
            return

        try:
            full_image = await self.client.get_photo_images(
                self.installation, thumbnail.id_signal, thumbnail.signal_type
            )
        except (
            APIConnectionError, APIResponseError,
            WAFBlockedError, SessionExpiredError,
        ) as err:
            _LOGGER.debug(
                "Full image fetch failed for %s: %s", camera.name, err.message
            )
            return

        if full_image is not None and len(full_image) > len(
            self.camera_images.get(camera.zone_id, b"")
        ):
            self.camera_images[camera.zone_id] = _overlay_text(
                full_image, camera.name, timestamp
            )
            _LOGGER.info(
                "Upgraded %s to full image: %d bytes",
                camera.name, len(full_image),
            )

    async def _fetch_thumbnail(self, camera: CameraDevice) -> None:
        """Fetch the latest cached thumbnail for a camera."""
        try:
            thumbnail = await self.client.get_thumbnail(
                self.installation, camera
            )
        except (
            APIConnectionError,
            APIResponseError,
            WAFBlockedError,
            SessionExpiredError,
        ) as err:
            _LOGGER.warning(
                "Thumbnail fetch failed for %s: %s",
                camera.name, err.message,
            )
            return

        if not thumbnail.image:
            return

        decoded = base64.b64decode(thumbnail.image)
        if len(decoded) < 2 or decoded[0] != 0xFF or decoded[1] != 0xD8:
            _LOGGER.warning(
                "Thumbnail for %s is not valid JPEG", camera.name
            )
            return

        # Parse timestamp from API or use now
        if thumbnail.timestamp:
            try:
                ts = datetime.fromisoformat(thumbnail.timestamp)
            except ValueError:
                ts = datetime.now()
            self.camera_timestamps[camera.zone_id] = thumbnail.timestamp
        else:
            ts = datetime.now()
            self.camera_timestamps[camera.zone_id] = ts.isoformat()

        self.camera_images[camera.zone_id] = await self.hass.async_add_executor_job(
            _overlay_text, decoded, camera.name, ts
        )
        _LOGGER.info(
            "Loaded cached thumbnail for %s: %d bytes", camera.name, len(decoded)
        )
