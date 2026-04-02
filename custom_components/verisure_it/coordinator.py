"""DataUpdateCoordinator for Verisure Italy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from aiohttp import ClientSession

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from verisure_api import (
    AlarmState,
    AuthenticationError,
    GeneralStatus,
    Installation,
    ProtoCode,
    SessionExpiredError,
    VerisureClient,
    WAFBlockedError,
    parse_proto_code,
)
from verisure_api.exceptions import APIConnectionError, UnexpectedStateError
from verisure_api.models import PROTO_TO_STATE, ZoneException

from .const import (
    CONF_DEVICE_ID,
    CONF_INSTALLATION_ALIAS,
    CONF_INSTALLATION_NUMBER,
    CONF_INSTALLATION_PANEL,
    CONF_UUID,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


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
        poll_interval = config_entry.options.get(
            "poll_interval", DEFAULT_POLL_INTERVAL
        )
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
        except (APIConnectionError, WAFBlockedError) as err:
            raise UpdateFailed(err.message) from err
        except UnexpectedStateError as err:
            _LOGGER.error("Unexpected alarm state: %s", err.proto_code)
            raise UpdateFailed(err.message) from err

        proto = parse_proto_code(status.status)
        alarm_state = PROTO_TO_STATE[proto]

        return VerisureStatusData(
            alarm_state=alarm_state,
            proto_code=proto,
            timestamp=status.timestamp_update,
            exceptions=status.exceptions or [],
        )
