"""Config flow for Verisure Italy."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from aiohttp import ClientSession
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback

from verisure_italy import (
    AuthenticationError,
    Installation,
    OtpPhone,
    TwoFactorRequiredError,
    VerisureClient,
    generate_device_id,
    generate_uuid,
)

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


class VerisureItConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Verisure Italy."""

    VERSION = 1

    def __init__(self) -> None:
        self._client: VerisureClient | None = None
        self._session: ClientSession | None = None
        self._device_id: str = ""
        self._uuid: str = ""
        self._username: str = ""
        self._password: str = ""
        self._otp_hash: str = ""
        self._otp_phones: list[OtpPhone] = []
        self._installations: list[Installation] = []

    async def _get_client(self) -> VerisureClient:
        """Get or create the API client."""
        if self._client is None:
            self._device_id = generate_device_id()
            self._uuid = generate_uuid()
            self._session = ClientSession()
            self._client = VerisureClient(
                username=self._username,
                password=self._password,
                http_session=self._session,
                device_id=self._device_id,
                uuid=self._uuid,
                id_device_indigitall="",
            )
        return self._client

    async def _cleanup_session(self) -> None:
        """Close the temporary session."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Username and password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            client = await self._get_client()
            try:
                await client.login()
                return await self.async_step_installation()
            except TwoFactorRequiredError:
                # Need 2FA — get OTP challenge
                otp_hash, phones = await client.validate_device(None, None)
                if otp_hash is not None:
                    self._otp_hash = otp_hash
                    self._otp_phones = phones
                return await self.async_step_2fa()
            except AuthenticationError as err:
                _LOGGER.error("Authentication failed: %s", err.message)
                errors["base"] = "invalid_auth"
                await self._cleanup_session()
                self._client = None

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
        )

    async def async_step_2fa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Two-factor authentication."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = await self._get_client()
            sms_code = user_input["code"]

            try:
                await client.validate_device(self._otp_hash, sms_code)
                # Verisure IT: hash=null on validate — re-login
                await client.login()
                return await self.async_step_installation()
            except AuthenticationError as err:
                _LOGGER.error("2FA failed: %s", err.message)
                errors["base"] = "invalid_code"

        # Auto-send OTP to first phone on initial display
        if user_input is None and len(self._otp_phones) >= 1:
            client = await self._get_client()
            await client.send_otp(
                self._otp_phones[0].id, self._otp_hash
            )

        return self.async_show_form(
            step_id="2fa",
            data_schema=vol.Schema({
                vol.Required("code"): str,
            }),
            errors=errors,
            description_placeholders={
                "phone": self._otp_phones[0].phone if self._otp_phones else "unknown",
            },
        )

    async def async_step_installation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: Select installation."""
        client = await self._get_client()

        if not self._installations:
            self._installations = await client.list_installations()

        # Auto-select if only one
        if len(self._installations) == 1:
            inst = self._installations[0]
            await client.get_services(inst)
            await self._cleanup_session()
            return self._create_entry(inst)

        if user_input is not None:
            number = user_input["installation"]
            inst = next(
                i for i in self._installations if i.number == number
            )
            await client.get_services(inst)
            await self._cleanup_session()
            return self._create_entry(inst)

        options = {
            i.number: f"{i.alias} ({i.address})"
            for i in self._installations
        }

        return self.async_show_form(
            step_id="installation",
            data_schema=vol.Schema({
                vol.Required("installation"): vol.In(options),
            }),
        )

    def _create_entry(self, installation: Installation) -> ConfigFlowResult:
        """Create the config entry."""
        return self.async_create_entry(
            title=installation.alias,
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_DEVICE_ID: self._device_id,
                CONF_UUID: self._uuid,
                CONF_INSTALLATION_NUMBER: installation.number,
                CONF_INSTALLATION_PANEL: installation.panel,
                CONF_INSTALLATION_ALIAS: installation.alias,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        """Get the options flow."""
        return VerisureItOptionsFlow()


class VerisureItOptionsFlow(OptionsFlow):
    """Options flow for Verisure Italy."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_interval = self.config_entry.options.get(
            "poll_interval", DEFAULT_POLL_INTERVAL
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(
                    "poll_interval", default=current_interval
                ): vol.All(int, vol.Range(min=3, max=300)),
            }),
        )
