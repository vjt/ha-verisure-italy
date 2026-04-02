"""Verisure IT API client.

Typed async client for the Verisure Italy GraphQL API. Every response
is parsed into a Pydantic model directly from JSON. If the response
doesn't match: ValidationError. No dicts, no Any, no negotiation.

Takes an aiohttp ClientSession via constructor injection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from functools import partial

import jwt
from aiohttp import ClientConnectorError, ClientSession
from pydantic import ValidationError

from .exceptions import (
    APIConnectionError,
    APIResponseError,
    AuthenticationError,
    OperationFailedError,
    OperationTimeoutError,
    SessionExpiredError,
    TwoFactorRequiredError,
    WAFBlockedError,
)
from .graphql import (
    ARM_PANEL_MUTATION,
    ARM_STATUS_QUERY,
    CHECK_ALARM_QUERY,
    CHECK_ALARM_STATUS_QUERY,
    DISARM_PANEL_MUTATION,
    DISARM_STATUS_QUERY,
    GENERAL_STATUS_QUERY,
    INSTALLATION_LIST_QUERY,
    LOGIN_TOKEN_MUTATION,
    LOGOUT_MUTATION,
    SEND_OTP_MUTATION,
    SERVICES_QUERY,
    VALIDATE_DEVICE_MUTATION,
)
from .models import (
    STATE_TO_COMMAND,
    AlarmState,
    ArmCommand,
    ArmResult,
    DisarmResult,
    GeneralStatus,
    Installation,
    LoginResponse,
    OperationResult,
    OtpPhone,
    Service,
)
from .responses import (
    ArmPanelEnvelope,
    ArmStatusEnvelope,
    CheckAlarmEnvelope,
    CheckAlarmStatusEnvelope,
    DisarmPanelEnvelope,
    DisarmStatusEnvelope,
    ErrorResponse,
    GeneralStatusEnvelope,
    InstallationListEnvelope,
    LoginEnvelope,
    SendOtpEnvelope,
    ServicesEnvelope,
    ValidateDeviceEnvelope,
)

_LOGGER = logging.getLogger(__name__)

API_URL = "https://customers.verisure.it/owa-api/graphql"
API_CALLBY = "OWA_10"
API_COUNTRY = "IT"
API_LANG = "it"

DEVICE_BRAND = "samsung"
DEVICE_NAME = "SM-S901U"
DEVICE_OS_VERSION = "12"
DEVICE_VERSION = "10.102.0"

ALARM_STATUS_SERVICE_ID = "11"
DEFAULT_POLL_DELAY: float = 2.0
DEFAULT_POLL_TIMEOUT: float = 30.0

# Type aliases
PollFn = Callable[[Installation, str, int], Awaitable[OperationResult]]
GraphQLVars = dict[str, str | int | bool]
GraphQLContent = dict[str, str | GraphQLVars]


def generate_uuid() -> str:
    """Generate a device UUID for the API."""
    from uuid import uuid4

    return str(uuid4()).replace("-", "")[0:16]


def generate_device_id() -> str:
    """Generate a device identifier for the API."""
    return secrets.token_urlsafe(16) + ":APA91b" + secrets.token_urlsafe(130)[0:134]


class VerisureClient:
    """Typed async client for the Verisure Italy GraphQL API.

    All public methods return Pydantic models or raise typed exceptions.
    No dicts leak out. No silent failures.
    """

    def __init__(
        self,
        username: str,
        password: str,
        http_session: ClientSession,
        device_id: str,
        uuid: str,
        id_device_indigitall: str,
        poll_delay: float = DEFAULT_POLL_DELAY,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT,
    ) -> None:
        self._username = username
        self._password = password
        self._http = http_session
        self._device_id = device_id
        self._uuid = uuid
        self._id_device_indigitall = id_device_indigitall
        self._poll_delay = poll_delay
        self._poll_timeout = poll_timeout

        self._auth_token: str | None = None
        self._auth_token_exp: datetime = datetime.min
        self._login_timestamp: int = 0
        self._refresh_token: str = ""
        self._otp_challenge: tuple[str, str] | None = None

        self._capabilities: dict[str, str] = {}
        self._capabilities_exp: dict[str, datetime] = {}
        self._last_proto: str = ""
        self._apollo_operation_id: str = secrets.token_hex(64)

    # -------------------------------------------------------------------
    # HTTP transport — returns raw response text, never dicts
    # -------------------------------------------------------------------

    async def _execute(
        self,
        content: GraphQLContent,
        operation: str,
        installation: Installation | None,
    ) -> str:
        """Send a GraphQL request. Returns raw JSON response text.

        Raises APIConnectionError, WAFBlockedError, or APIResponseError.
        Callers parse the text into typed Pydantic envelopes.
        """
        headers = self._build_headers(operation, installation)
        _LOGGER.debug("[%s] Executing request", operation)

        try:
            async with self._http.post(
                API_URL, headers=headers, json=content
            ) as resp:
                http_status = resp.status
                response_text = await resp.text()
        except ClientConnectorError as err:
            raise APIConnectionError(
                f"Connection error with {API_URL}: {err}"
            ) from err

        if http_status == 403:
            if "_Incapsula_Resource" in response_text:
                raise WAFBlockedError(
                    "Blocked by Incapsula WAF — back off and retry"
                )
            raise APIResponseError(
                f"HTTP 403 from Verisure API ({operation})",
                graphql_errors=None,
                http_status=403,
            )

        if http_status >= 400:
            raise APIResponseError(
                f"HTTP {http_status} from Verisure API ({operation})",
                graphql_errors=None,
                http_status=http_status,
            )

        # Check for GraphQL errors before returning
        self._check_graphql_errors(response_text, operation)

        return response_text

    def _check_graphql_errors(self, response_text: str, operation: str) -> None:
        """Parse error responses and raise specific exceptions.

        Only called when we need to check for errors. If parsing as
        ErrorResponse fails, the response is not an error — move on.
        """
        try:
            error_resp = ErrorResponse.model_validate_json(response_text)
        except ValidationError:
            return  # Not an error response shape

        if not error_resp.errors:
            return

        first = error_resp.errors[0]

        # Check for session expiry
        if first.data is not None and first.data.status == 403:
            raise SessionExpiredError(f"Session expired during {operation}")

        # Check for 2FA requirement
        if first.data is not None and first.data.need_device_authorization:
            raise TwoFactorRequiredError("2FA authentication required")

        # Check for OTP data (don't raise — caller extracts it)
        if first.data is not None and first.data.auth_otp_hash is not None:
            return  # Caller will parse OTP data from the response

        # Old-style error with reason
        if first.data is not None and first.data.reason is not None:
            raise APIResponseError(
                first.data.reason,
                graphql_errors=None,
                http_status=None,
            )

        if first.message:
            raise APIResponseError(
                first.message,
                graphql_errors=None,
                http_status=None,
            )

    def _build_headers(
        self,
        operation: str,
        installation: Installation | None,
    ) -> dict[str, str]:
        """Build request headers."""
        headers: dict[str, str] = {
            "app": json.dumps({"appVersion": DEVICE_VERSION, "origin": "native"}),
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 12; SM-S901U)"
                " AppleWebKit/537.36 (KHTML, like Gecko)"
                " Chrome/102.0.5005.124 Mobile Safari/537.36"
            ),
            "X-APOLLO-OPERATION-ID": self._apollo_operation_id,
            "X-APOLLO-OPERATION-NAME": operation,
            "extension": '{"mode":"full"}',
        }

        if installation is not None:
            headers["numinst"] = installation.number
            headers["panel"] = installation.panel
            cap = self._capabilities.get(installation.number, "")
            if cap:
                headers["X-Capabilities"] = cap

        if self._auth_token:
            headers["auth"] = json.dumps({
                "loginTimestamp": self._login_timestamp,
                "user": self._username,
                "id": self._generate_request_id(),
                "country": API_COUNTRY,
                "lang": API_LANG,
                "callby": API_CALLBY,
                "hash": self._auth_token,
            })

        if operation in ("mkValidateDevice", "RefreshLogin", "mkSendOTP"):
            headers["auth"] = json.dumps({
                "loginTimestamp": self._login_timestamp,
                "user": self._username,
                "id": self._generate_request_id(),
                "country": API_COUNTRY,
                "lang": API_LANG,
                "callby": API_CALLBY,
                "hash": "",
                "refreshToken": "",
            })

        if self._otp_challenge is not None:
            headers["security"] = json.dumps({
                "token": self._otp_challenge[1],
                "type": "OTP",
                "otpHash": self._otp_challenge[0],
            })

        return headers

    def _generate_request_id(self) -> str:
        now = datetime.now()
        return (
            f"OWA_______________{self._username}_______________"
            f"{now.year}{now.month}{now.day}{now.hour}"
            f"{now.minute}{now.microsecond}"
        )

    # -------------------------------------------------------------------
    # Auth token management
    # -------------------------------------------------------------------

    def _decode_jwt_expiry(self, token: str) -> datetime:
        """Decode JWT to extract expiry. Raises AuthenticationError."""
        try:
            decoded: dict[str, str | int | float] = jwt.decode(  # type: ignore[reportUnknownMemberType]
                token,
                options={"verify_signature": False},
                algorithms=["EdDSA", "HS256"],
            )
        except jwt.exceptions.DecodeError as err:
            raise AuthenticationError(
                f"Failed to decode JWT: {err}"
            ) from err

        exp = decoded.get("exp")
        if not isinstance(exp, (int, float)):
            raise AuthenticationError("JWT missing exp claim")
        return datetime.fromtimestamp(exp)

    async def _ensure_auth(self, installation: Installation) -> None:
        """Ensure both auth and capabilities tokens are valid."""
        token_expiring = (
            datetime.now() + timedelta(minutes=1) > self._auth_token_exp
        )
        if self._auth_token is None or token_expiring:
            await self.login()

        cap_exp = self._capabilities_exp.get(
            installation.number, datetime.min
        )
        if datetime.now() + timedelta(minutes=1) > cap_exp:
            await self.get_services(installation)

    # -------------------------------------------------------------------
    # Authentication
    # -------------------------------------------------------------------

    async def login(self) -> LoginResponse:
        """Authenticate. Returns LoginResponse or raises.

        Raises AuthenticationError on bad credentials.
        Raises TwoFactorRequiredError if 2FA is needed.
        """
        content: GraphQLContent = {
            "operationName": "mkLoginToken",
            "variables": {
                "user": self._username,
                "password": self._password,
                "id": self._generate_request_id(),
                "country": API_COUNTRY,
                "callby": API_CALLBY,
                "lang": API_LANG,
                "idDevice": self._device_id,
                "idDeviceIndigitall": self._id_device_indigitall,
                "deviceType": "",
                "deviceVersion": DEVICE_VERSION,
                "deviceResolution": "",
                "deviceName": DEVICE_NAME,
                "deviceBrand": DEVICE_BRAND,
                "deviceOsVersion": DEVICE_OS_VERSION,
                "uuid": self._uuid,
            },
            "query": LOGIN_TOKEN_MUTATION,
        }

        try:
            response_text = await self._execute(content, "mkLoginToken", None)
        except (TwoFactorRequiredError, SessionExpiredError):
            raise
        except APIResponseError as err:
            raise AuthenticationError(
                f"Login failed: {err.message}"
            ) from err

        envelope = LoginEnvelope.model_validate_json(response_text)
        result = envelope.data.xSLoginToken

        if result.need_device_authorization:
            raise TwoFactorRequiredError("2FA authentication required")

        if result.hash is None:
            raise AuthenticationError("Login returned null auth token")

        self._auth_token = result.hash
        self._login_timestamp = int(datetime.now().timestamp() * 1000)
        self._auth_token_exp = self._decode_jwt_expiry(self._auth_token)

        if result.refresh_token:
            self._refresh_token = result.refresh_token

        _LOGGER.info(
            "Login successful, token expires %s", self._auth_token_exp
        )
        return result

    async def validate_device(
        self,
        otp_hash: str | None,
        sms_code: str | None,
    ) -> tuple[str | None, list[OtpPhone]]:
        """Validate device, optionally with OTP code.

        Returns (otp_hash, phones) if OTP challenge is needed.
        Returns (None, []) if validation succeeded.
        """
        content: GraphQLContent = {
            "operationName": "mkValidateDevice",
            "variables": {
                "idDevice": self._device_id,
                "idDeviceIndigitall": self._id_device_indigitall,
                "uuid": self._uuid,
                "deviceName": DEVICE_NAME,
                "deviceBrand": DEVICE_BRAND,
                "deviceOsVersion": DEVICE_OS_VERSION,
                "deviceVersion": DEVICE_VERSION,
            },
            "query": VALIDATE_DEVICE_MUTATION,
        }

        if otp_hash is not None and sms_code is not None:
            self._otp_challenge = (otp_hash, sms_code)

        try:
            response_text = await self._execute(
                content, "mkValidateDevice", None
            )
            self._otp_challenge = None
        except APIResponseError as err:
            self._otp_challenge = None
            # Error may contain OTP challenge data
            return self._try_extract_otp(err)

        # Check if response contains OTP challenge (Unauthorized with phones)
        try:
            error_resp = ErrorResponse.model_validate_json(response_text)
            for error in error_resp.errors:
                if error.data and error.data.auth_otp_hash:
                    phones = error.data.auth_phones or []
                    return (error.data.auth_otp_hash, phones)
        except ValidationError:
            pass  # Not an error response — continue to parse success

        envelope = ValidateDeviceEnvelope.model_validate_json(response_text)
        result = envelope.data.xSValidateDevice

        # Successful validation may return hash=null (Verisure IT).
        # This means the device is now authorized — caller must login()
        # again to obtain the actual auth token.
        if result.hash is not None:
            self._auth_token = result.hash
            self._auth_token_exp = self._decode_jwt_expiry(self._auth_token)
            if result.refresh_token:
                self._refresh_token = result.refresh_token

        return (None, [])

    def _try_extract_otp(
        self, err: APIResponseError
    ) -> tuple[str | None, list[OtpPhone]]:
        """Try to extract OTP data from an error. Re-raises if not OTP."""
        # The graphql_errors on our exception are already typed
        # but we need to re-parse from the original response
        # For now, just re-raise — OTP extraction needs the raw response
        raise AuthenticationError(
            f"Device validation failed: {err.message}"
        ) from err

    async def send_otp(self, phone_id: int, otp_hash: str) -> bool:
        """Request an OTP SMS. Returns True on success."""
        content: GraphQLContent = {
            "operationName": "mkSendOTP",
            "variables": {"recordId": phone_id, "otpHash": otp_hash},
            "query": SEND_OTP_MUTATION,
        }
        response_text = await self._execute(content, "mkSendOTP", None)
        envelope = SendOtpEnvelope.model_validate_json(response_text)
        return envelope.data.xSSendOtp.res == "OK"

    async def logout(self) -> None:
        """Logout and clear auth state."""
        empty_vars: GraphQLVars = {}
        content: GraphQLContent = {
            "operationName": "Logout",
            "variables": empty_vars,
            "query": LOGOUT_MUTATION,
        }
        try:
            await self._execute(content, "Logout", None)
        finally:
            self._auth_token = None
            self._auth_token_exp = datetime.min
            self._login_timestamp = 0
            self._refresh_token = ""

    # -------------------------------------------------------------------
    # Installation & Services
    # -------------------------------------------------------------------

    async def list_installations(self) -> list[Installation]:
        """List all installations. Raises on failure."""
        content: GraphQLContent = {
            "operationName": "mkInstallationList",
            "query": INSTALLATION_LIST_QUERY,
        }
        response_text = await self._execute(
            content, "mkInstallationList", None
        )
        envelope = InstallationListEnvelope.model_validate_json(response_text)
        return envelope.data.xSInstallations.installations

    async def get_services(
        self, installation: Installation
    ) -> list[Service]:
        """Get available services and refresh capabilities token."""
        content: GraphQLContent = {
            "operationName": "Srv",
            "variables": {
                "numinst": installation.number,
                "uuid": self._uuid,
            },
            "query": SERVICES_QUERY,
        }
        response_text = await self._execute(content, "Srv", None)
        envelope = ServicesEnvelope.model_validate_json(response_text)
        srv = envelope.data.xSSrv.installation

        # Update capabilities token
        self._capabilities[installation.number] = srv.capabilities
        self._capabilities_exp[installation.number] = self._decode_jwt_expiry(
            srv.capabilities
        )

        return srv.services

    # -------------------------------------------------------------------
    # Alarm Status
    # -------------------------------------------------------------------

    async def check_alarm(self, installation: Installation) -> str:
        """Initiate alarm status check. Returns reference ID for polling.

        WARNING: This pings the physical panel and creates a timeline
        entry in the Verisure app. Use get_general_status() for passive polling.
        """
        await self._ensure_auth(installation)
        content: GraphQLContent = {
            "operationName": "CheckAlarm",
            "variables": {
                "numinst": installation.number,
                "panel": installation.panel,
            },
            "query": CHECK_ALARM_QUERY,
        }
        response_text = await self._execute(
            content, "CheckAlarm", installation
        )
        envelope = CheckAlarmEnvelope.model_validate_json(response_text)
        return envelope.data.xSCheckAlarm.reference_id

    async def poll_alarm_status(
        self,
        installation: Installation,
        reference_id: str,
    ) -> OperationResult:
        """Poll alarm status until complete or timeout.

        Returns OperationResult or raises OperationTimeoutError.
        """
        return await self._poll_operation(
            installation,
            reference_id,
            self._check_alarm_status_once,
        )

    async def _check_alarm_status_once(
        self,
        installation: Installation,
        reference_id: str,
        counter: int,
    ) -> OperationResult:
        """Single alarm status poll."""
        content: GraphQLContent = {
            "operationName": "CheckAlarmStatus",
            "variables": {
                "numinst": installation.number,
                "panel": installation.panel,
                "referenceId": reference_id,
                "idService": ALARM_STATUS_SERVICE_ID,
                "counter": counter,
            },
            "query": CHECK_ALARM_STATUS_QUERY,
        }
        response_text = await self._execute(
            content, "CheckAlarmStatus", installation
        )
        envelope = CheckAlarmStatusEnvelope.model_validate_json(response_text)
        return envelope.data.xSCheckAlarmStatus

    async def get_general_status(
        self, installation: Installation
    ) -> GeneralStatus:
        """Get alarm status passively (does NOT ping the panel).

        Uses xSStatus which reads server-side cached state.
        Does NOT create timeline entries in the Verisure app.
        """
        await self._ensure_auth(installation)
        content: GraphQLContent = {
            "operationName": "Status",
            "variables": {"numinst": installation.number},
            "query": GENERAL_STATUS_QUERY,
        }
        response_text = await self._execute(
            content, "Status", installation
        )
        envelope = GeneralStatusEnvelope.model_validate_json(response_text)
        return envelope.data.xSStatus

    # -------------------------------------------------------------------
    # Arm / Disarm
    # -------------------------------------------------------------------

    async def arm(
        self,
        installation: Installation,
        target_state: AlarmState,
    ) -> ArmResult:
        """Arm the alarm. Polls until complete.

        Returns ArmResult or raises OperationTimeoutError/OperationFailedError.
        """
        command = STATE_TO_COMMAND[target_state]
        await self._ensure_auth(installation)

        content: GraphQLContent = {
            "operationName": "xSArmPanel",
            "variables": {
                "request": command.value,
                "numinst": installation.number,
                "panel": installation.panel,
                "currentStatus": self._last_proto,
            },
            "query": ARM_PANEL_MUTATION,
        }
        response_text = await self._execute(
            content, "xSArmPanel", installation
        )
        envelope = ArmPanelEnvelope.model_validate_json(response_text)
        arm_resp = envelope.data.xSArmPanel

        if arm_resp.res != "OK":
            raise OperationFailedError(
                f"Arm rejected: {arm_resp.msg}",
                error_code=None,
                error_type=None,
            )

        poll_fn = partial(self._check_arm_status_once, command=command)
        result = await self._poll_operation(
            installation, arm_resp.reference_id, poll_fn
        )

        # Re-parse as ArmResult (has extra fields vs OperationResult)
        # The poll already gave us an OperationResult; for arm we need
        # the richer ArmResult. But since poll returns OperationResult
        # and ArmResult has the same core fields, we use the proto.
        self._last_proto = result.protom_response
        return ArmResult(
            res=result.res,
            msg=result.msg,
            status=result.status,
            numinst=result.numinst,
            protomResponse=result.protom_response,
            protomResponseDate=result.protom_response_data,
            requestId="",
            error=None,
        )

    async def _check_arm_status_once(
        self,
        installation: Installation,
        reference_id: str,
        counter: int,
        command: ArmCommand,
    ) -> OperationResult:
        """Single arm status poll."""
        content: GraphQLContent = {
            "operationName": "ArmStatus",
            "variables": {
                "request": command.value,
                "numinst": installation.number,
                "panel": installation.panel,
                "currentStatus": self._last_proto,
                "referenceId": reference_id,
                "counter": counter,
            },
            "query": ARM_STATUS_QUERY,
        }
        response_text = await self._execute(
            content, "ArmStatus", installation
        )
        envelope = ArmStatusEnvelope.model_validate_json(response_text)
        arm_result = envelope.data.xSArmStatus
        # Return as OperationResult for the generic poll machinery
        return OperationResult(
            res=arm_result.res,
            msg=arm_result.msg,
            status=arm_result.status,
            numinst=arm_result.numinst,
            protomResponse=arm_result.protom_response,
            protomResponseDate=arm_result.protom_response_data,
        )

    async def disarm(self, installation: Installation) -> DisarmResult:
        """Disarm the alarm completely. Polls until complete.

        Returns DisarmResult or raises OperationTimeoutError/OperationFailedError.
        """
        command = ArmCommand.DISARM_ALL
        await self._ensure_auth(installation)

        content: GraphQLContent = {
            "operationName": "xSDisarmPanel",
            "variables": {
                "request": command.value,
                "numinst": installation.number,
                "panel": installation.panel,
                "currentStatus": self._last_proto,
            },
            "query": DISARM_PANEL_MUTATION,
        }
        response_text = await self._execute(
            content, "xSDisarmPanel", installation
        )
        envelope = DisarmPanelEnvelope.model_validate_json(response_text)
        disarm_resp = envelope.data.xSDisarmPanel

        if disarm_resp.res != "OK":
            raise OperationFailedError(
                f"Disarm rejected: {disarm_resp.msg}",
                error_code=None,
                error_type=None,
            )

        poll_fn = partial(self._check_disarm_status_once, command=command)
        result = await self._poll_operation(
            installation, disarm_resp.reference_id, poll_fn
        )

        self._last_proto = result.protom_response
        return DisarmResult(
            res=result.res,
            msg=result.msg,
            numinst=result.numinst,
            protomResponse=result.protom_response,
            protomResponseDate=result.protom_response_data,
            requestId="",
            error=None,
        )

    async def _check_disarm_status_once(
        self,
        installation: Installation,
        reference_id: str,
        counter: int,
        command: ArmCommand,
    ) -> OperationResult:
        """Single disarm status poll."""
        content: GraphQLContent = {
            "operationName": "DisarmStatus",
            "variables": {
                "request": command.value,
                "numinst": installation.number,
                "panel": installation.panel,
                "currentStatus": self._last_proto,
                "referenceId": reference_id,
                "counter": counter,
            },
            "query": DISARM_STATUS_QUERY,
        }
        response_text = await self._execute(
            content, "DisarmStatus", installation
        )
        envelope = DisarmStatusEnvelope.model_validate_json(response_text)
        disarm_result = envelope.data.xSDisarmStatus
        return OperationResult(
            res=disarm_result.res,
            msg=disarm_result.msg,
            status=None,
            numinst=disarm_result.numinst,
            protomResponse=disarm_result.protom_response,
            protomResponseDate=disarm_result.protom_response_data,
        )

    # -------------------------------------------------------------------
    # Generic polling — typed, bounded, no Any
    # -------------------------------------------------------------------

    async def _poll_operation(
        self,
        installation: Installation,
        reference_id: str,
        poll_fn: Callable[
            [Installation, str, int], Awaitable[OperationResult]
        ],
    ) -> OperationResult:
        """Poll until complete or timeout.

        Returns OperationResult or raises OperationTimeoutError.
        """

        async def _do_poll() -> OperationResult:
            counter = 1
            while True:
                await asyncio.sleep(self._poll_delay)
                result = await poll_fn(installation, reference_id, counter)
                if not result.is_pending:
                    self._last_proto = result.protom_response
                    return result
                counter += 1

        try:
            return await asyncio.wait_for(
                _do_poll(), timeout=self._poll_timeout
            )
        except TimeoutError:
            raise OperationTimeoutError(
                f"Operation did not complete within {self._poll_timeout}s. "
                f"Fail-secure: assuming previous state is still active."
            ) from None
