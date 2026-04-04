"""Verisure IT API client.

Typed async client for the Verisure Italy GraphQL API. Every response
is parsed into a Pydantic model directly from JSON. If the response
doesn't match: ValidationError. No dicts, no Any, no negotiation.

Takes an aiohttp ClientSession via constructor injection.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import partial

import jwt
from aiohttp import ClientConnectorError, ClientSession
from pydantic import ValidationError

from .exceptions import (
    APIConnectionError,
    APIResponseError,
    ArmingExceptionError,
    AuthenticationError,
    ImageCaptureError,
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
    DEVICE_LIST_QUERY,
    DISARM_PANEL_MUTATION,
    DISARM_STATUS_QUERY,
    GENERAL_STATUS_QUERY,
    GET_EXCEPTIONS_QUERY,
    GET_PHOTO_IMAGES_QUERY,
    GET_THUMBNAIL_QUERY,
    INSTALLATION_LIST_QUERY,
    LOGIN_TOKEN_MUTATION,
    LOGOUT_MUTATION,
    REQUEST_IMAGES_MUTATION,
    REQUEST_IMAGES_STATUS_QUERY,
    SEND_OTP_MUTATION,
    SERVICES_QUERY,
    VALIDATE_DEVICE_MUTATION,
)
from .models import (
    CAMERA_DEVICE_TYPES,
    CAMERA_IMAGE_DEVICE_TYPE,
    CAMERA_IMAGE_MEDIA_TYPE,
    CAMERA_IMAGE_RESOLUTION,
    STATE_TO_COMMAND,
    AlarmState,
    ArmCommand,
    ArmResult,
    CameraDevice,
    DisarmResult,
    GeneralStatus,
    Installation,
    LoginResponse,
    OperationResult,
    OtpPhone,
    Service,
    Thumbnail,
    ZoneException,
)
from .responses import (
    ArmPanelEnvelope,
    ArmStatusEnvelope,
    CheckAlarmEnvelope,
    CheckAlarmStatusEnvelope,
    DeviceListEnvelope,
    DisarmPanelEnvelope,
    DisarmStatusEnvelope,
    ErrorResponse,
    GeneralStatusEnvelope,
    GetExceptionsEnvelope,
    InstallationListEnvelope,
    LoginEnvelope,
    PhotoImagesEnvelope,
    RequestImagesEnvelope,
    RequestImagesStatusEnvelope,
    SendOtpEnvelope,
    ServicesEnvelope,
    ThumbnailEnvelope,
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
DEFAULT_POLL_TIMEOUT: float = 60.0

# Type aliases
PollFn = Callable[[Installation, str, int], Awaitable[OperationResult]]
GraphQLVars = dict[str, str | int | bool | list[int]]
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
        self._auth_token_exp: datetime = datetime.min.replace(tzinfo=UTC)
        self._login_timestamp: int = 0
        self._refresh_token: str = ""
        self._otp_challenge: tuple[str, str] | None = None

        self._capabilities: dict[str, str] = {}
        self._capabilities_exp: dict[str, datetime] = {}
        self._last_proto: str = ""
        self._apollo_operation_id: str = secrets.token_hex(64)
        self._auth_lock = asyncio.Lock()

    def set_poll_params(
        self, *, timeout: float | None = None, delay: float | None = None
    ) -> None:
        """Update poll parameters at runtime."""
        if timeout is not None:
            self._poll_timeout = timeout
        if delay is not None:
            self._poll_delay = delay

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
                http_status=403,
            )

        if http_status >= 400:
            raise APIResponseError(
                f"HTTP {http_status} from Verisure API ({operation})",
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
                http_status=None,
            )

        if first.message:
            raise APIResponseError(
                first.message,
                http_status=None,
            )

        # Catch-all: errors list is non-empty but no branch handled it
        raise APIResponseError(
            f"Unknown GraphQL error during {operation}: {first}",
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
        return datetime.fromtimestamp(exp, tz=UTC)

    async def _ensure_auth(self, installation: Installation) -> None:
        """Ensure both auth and capabilities tokens are valid.

        Uses a lock to prevent concurrent callers from racing on token refresh.
        """
        async with self._auth_lock:
            token_expiring = (
                datetime.now(tz=UTC) + timedelta(minutes=1) > self._auth_token_exp
            )
            if self._auth_token is None or token_expiring:
                await self.login()

            cap_exp = self._capabilities_exp.get(
                installation.number, datetime.min.replace(tzinfo=UTC)
            )
            if datetime.now(tz=UTC) + timedelta(minutes=1) > cap_exp:
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
            raise AuthenticationError(
                f"Device validation failed: {err.message}"
            ) from err

        # Check if response contains OTP challenge (Unauthorized with phones)
        try:
            error_resp = ErrorResponse.model_validate_json(response_text)
            for error in error_resp.errors:
                if error.data is not None and error.data.auth_otp_hash is not None:
                    phones = error.data.auth_phones if error.data.auth_phones is not None else []
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
            self._auth_token_exp = datetime.min.replace(tzinfo=UTC)
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
        force_arming_remote_id: str | None = None,
        suid: str | None = None,
    ) -> ArmResult:
        """Arm the alarm. Polls until complete.

        Returns ArmResult or raises OperationTimeoutError/OperationFailedError.
        Raises ArmingExceptionError if open zones detected (NON_BLOCKING with
        allowForcing). Caller can retry with force_arming_remote_id + suid
        from the exception to override.
        """
        command = STATE_TO_COMMAND[target_state]
        await self._ensure_auth(installation)

        variables: GraphQLVars = {
            "request": command.value,
            "numinst": installation.number,
            "panel": installation.panel,
            "currentStatus": self._last_proto,
        }
        if force_arming_remote_id is not None:
            variables["forceArmingRemoteId"] = force_arming_remote_id
        if suid is not None:
            variables["suid"] = suid

        content: GraphQLContent = {
            "operationName": "xSArmPanel",
            "variables": variables,
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

        poll_fn = partial(
            self._check_arm_status_once,
            command=command,
            force_arming_remote_id=force_arming_remote_id,
        )
        result = await self._poll_operation(
            installation, arm_resp.reference_id, poll_fn
        )

        # Poll completed successfully — proto fields must be present.
        # If they're None after a non-WAIT, non-ERROR result, the API
        # returned something unexpected and we crash loud.
        if result.protom_response is None or result.protom_response_data is None:
            raise APIResponseError(
                "Arm completed but response missing proto fields",
                http_status=None,
            )

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
        force_arming_remote_id: str | None = None,
    ) -> OperationResult:
        """Single arm status poll."""
        variables: GraphQLVars = {
            "request": command.value,
            "numinst": installation.number,
            "panel": installation.panel,
            "currentStatus": self._last_proto,
            "referenceId": reference_id,
            "counter": counter,
        }
        if force_arming_remote_id is not None:
            variables["forceArmingRemoteId"] = force_arming_remote_id

        content: GraphQLContent = {
            "operationName": "ArmStatus",
            "variables": variables,
            "query": ARM_STATUS_QUERY,
        }
        response_text = await self._execute(
            content, "ArmStatus", installation
        )
        envelope = ArmStatusEnvelope.model_validate_json(response_text)
        arm_result = envelope.data.xSArmStatus

        # Detect force-arm-eligible error BEFORE converting to OperationResult
        if (
            arm_result.res == "ERROR"
            and arm_result.error is not None
            and arm_result.error.type == "NON_BLOCKING"
            and arm_result.error.allow_forcing
            and arm_result.error.reference_id is not None
        ):
            suid = arm_result.error.suid or ""
            exceptions = await self._get_exceptions(
                installation, arm_result.error.reference_id, suid
            )
            raise ArmingExceptionError(
                arm_result.error.reference_id, suid, exceptions
            )

        # Return as OperationResult for the generic poll machinery
        return OperationResult(
            res=arm_result.res,
            msg=arm_result.msg,
            status=arm_result.status,
            numinst=arm_result.numinst,
            protomResponse=arm_result.protom_response,
            protomResponseDate=arm_result.protom_response_data,
        )

    async def _get_exceptions(
        self,
        installation: Installation,
        reference_id: str,
        suid: str,
    ) -> list[ZoneException]:
        """Fetch arming exception details (open zones).

        Polls xSGetExceptions until OK or timeout. Returns zone list.
        """
        counter = 1
        max_polls = max(10, round(self._poll_timeout / max(1, self._poll_delay)))

        while counter <= max_polls:
            content: GraphQLContent = {
                "operationName": "xSGetExceptions",
                "variables": {
                    "numinst": installation.number,
                    "panel": installation.panel,
                    "referenceId": reference_id,
                    "counter": counter,
                    "suid": suid,
                },
                "query": GET_EXCEPTIONS_QUERY,
            }
            response_text = await self._execute(
                content, "xSGetExceptions", installation
            )
            envelope = GetExceptionsEnvelope.model_validate_json(response_text)
            result = envelope.data.xSGetExceptions

            if result.res == "OK":
                return result.exceptions or []

            if result.res != "WAIT":
                _LOGGER.warning(
                    "Unexpected xSGetExceptions result: %s (msg=%s)",
                    result.res, result.msg,
                )
                return []

            await asyncio.sleep(self._poll_delay)
            counter += 1

        _LOGGER.warning(
            "xSGetExceptions timed out after %d polls — zone details unavailable",
            max_polls,
        )
        return []

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

        if result.protom_response is None or result.protom_response_data is None:
            raise APIResponseError(
                "Disarm completed but response missing proto fields",
                http_status=None,
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
    # Camera / Images
    # -------------------------------------------------------------------

    async def list_camera_devices(
        self, installation: Installation
    ) -> list[CameraDevice]:
        """List active camera devices. Filters xSDeviceList for camera types.

        Returns CameraDevice list or raises on API failure.
        """
        await self._ensure_auth(installation)
        content: GraphQLContent = {
            "operationName": "xSDeviceList",
            "variables": {
                "numinst": installation.number,
                "panel": installation.panel,
            },
            "query": DEVICE_LIST_QUERY,
        }
        response_text = await self._execute(
            content, "xSDeviceList", installation
        )
        envelope = DeviceListEnvelope.model_validate_json(response_text)
        raw_devices = envelope.data.xSDeviceList.devices

        cameras: list[CameraDevice] = []
        for raw in raw_devices:
            if raw.device_type not in CAMERA_DEVICE_TYPES:
                continue
            if not raw.is_active:
                continue

            if not raw.code.isdigit():
                _LOGGER.warning(
                    "Camera %s has non-numeric code %r, skipping",
                    raw.name, raw.code,
                )
                continue
            code = int(raw.code)
            zone_id = raw.zone_id or f"{raw.device_type}{code:02d}"

            cameras.append(
                CameraDevice(
                    id=raw.id,
                    code=code,
                    zone_id=zone_id,
                    name=raw.name,
                    device_type=raw.device_type,
                    serial_number=raw.serial_number,
                )
            )

        _LOGGER.info("Found %d camera devices", len(cameras))
        return cameras

    async def request_images(
        self,
        installation: Installation,
        camera: CameraDevice,
    ) -> str:
        """Request the panel to capture a new image. Returns reference ID.

        Raises OperationFailedError if the panel rejects the request.
        """
        await self._ensure_auth(installation)
        device_type_id = CAMERA_IMAGE_DEVICE_TYPE[camera.device_type]
        content: GraphQLContent = {
            "operationName": "RequestImages",
            "variables": {
                "numinst": installation.number,
                "panel": installation.panel,
                "devices": [camera.code],
                "resolution": CAMERA_IMAGE_RESOLUTION,
                "mediaType": CAMERA_IMAGE_MEDIA_TYPE,
                "deviceType": device_type_id,
            },
            "query": REQUEST_IMAGES_MUTATION,
        }
        response_text = await self._execute(
            content, "RequestImages", installation
        )
        envelope = RequestImagesEnvelope.model_validate_json(response_text)
        result = envelope.data.xSRequestImages

        if result.res != "OK":
            raise OperationFailedError(
                f"Image request rejected: {result.msg}",
                error_code=None,
                error_type=None,
            )
        return result.reference_id

    async def check_request_images_status(
        self,
        installation: Installation,
        camera: CameraDevice,
        reference_id: str,
        counter: int,
    ) -> bool:
        """Check status of image request. Returns True when complete."""
        content: GraphQLContent = {
            "operationName": "RequestImagesStatus",
            "variables": {
                "numinst": installation.number,
                "panel": installation.panel,
                "devices": [camera.code],
                "referenceId": reference_id,
                "counter": counter,
            },
            "query": REQUEST_IMAGES_STATUS_QUERY,
        }
        response_text = await self._execute(
            content, "RequestImagesStatus", installation
        )
        envelope = RequestImagesStatusEnvelope.model_validate_json(
            response_text
        )
        result = envelope.data.xSRequestImagesStatus

        if result.res == "ERROR":
            raise OperationFailedError(
                f"Image capture error: {result.msg}",
                error_code=None,
                error_type=None,
            )

        msg = result.msg or ""
        return "processing" not in msg and result.res != "WAIT"

    async def get_thumbnail(
        self,
        installation: Installation,
        camera: CameraDevice,
    ) -> Thumbnail:
        """Fetch the latest thumbnail image for a camera device."""
        await self._ensure_auth(installation)
        content: GraphQLContent = {
            "operationName": "mkGetThumbnail",
            "variables": {
                "numinst": installation.number,
                "panel": installation.panel,
                "device": camera.device_type,
                "zoneId": camera.zone_id,
            },
            "query": GET_THUMBNAIL_QUERY,
        }
        response_text = await self._execute(
            content, "mkGetThumbnail", installation
        )
        envelope = ThumbnailEnvelope.model_validate_json(response_text)
        return envelope.data.xSGetThumbnail

    async def get_photo_images(
        self,
        installation: Installation,
        id_signal: str,
        signal_type: str,
    ) -> bytes | None:
        """Fetch full-resolution image. Returns decoded JPEG bytes or None.

        Picks the largest BINARY image from the response and validates
        it starts with JPEG magic bytes (0xFFD8).
        """
        await self._ensure_auth(installation)
        content: GraphQLContent = {
            "operationName": "mkGetPhotoImages",
            "variables": {
                "numinst": installation.number,
                "idSignal": id_signal,
                "signalType": signal_type,
                "panel": installation.panel,
            },
            "query": GET_PHOTO_IMAGES_QUERY,
        }
        response_text = await self._execute(
            content, "mkGetPhotoImages", installation
        )
        envelope = PhotoImagesEnvelope.model_validate_json(response_text)
        devices = envelope.data.xSGetPhotoImages.devices
        if not devices:
            return None

        binary_images = [
            img
            for dev in devices
            for img in dev.images
            if img.type == "BINARY" and img.image
        ]
        if not binary_images:
            return None

        best = max(binary_images, key=lambda img: len(img.image))
        decoded = base64.b64decode(best.image)

        # Validate JPEG magic bytes
        if len(decoded) < 2 or decoded[0] != 0xFF or decoded[1] != 0xD8:
            _LOGGER.warning("Full image is not valid JPEG")
            return None

        return decoded

    async def capture_image(
        self,
        installation: Installation,
        camera: CameraDevice,
    ) -> bytes:
        """Request capture, wait for completion, return thumbnail JPEG bytes.

        Full flow: request → poll status → poll thumbnail until updated.
        Returns decoded JPEG bytes.
        Raises ImageCaptureError on timeout or invalid image data.
        Raises OperationFailedError if the panel rejects the capture request.
        """
        # Get baseline thumbnail to detect when new one arrives
        baseline = await self.get_thumbnail(installation, camera)
        baseline_id = baseline.id_signal
        baseline_image = baseline.image

        # Request capture — let OperationFailedError propagate
        reference_id = await self.request_images(installation, camera)

        async def _poll_capture() -> Thumbnail:
            # Wait for capture to complete
            counter = 1
            while True:
                await asyncio.sleep(self._poll_delay)
                done = await self.check_request_images_status(
                    installation, camera, reference_id, counter
                )
                if done:
                    break
                counter += 1

            # Wait for thumbnail to update (CDN propagation)
            while True:
                await asyncio.sleep(max(5, self._poll_delay))
                thumb = await self.get_thumbnail(installation, camera)
                # Detect update: idSignal changed, or for PIR cameras
                # (idSignal=None), image content changed
                if thumb.id_signal != baseline_id:
                    return thumb
                if baseline_id is None and thumb.image != baseline_image:
                    return thumb

        try:
            thumbnail = await asyncio.wait_for(
                _poll_capture(), timeout=self._poll_timeout
            )
        except TimeoutError:
            raise ImageCaptureError(
                f"Image capture timed out for {camera.name} "
                f"after {self._poll_timeout}s"
            ) from None

        if not thumbnail.image:
            raise ImageCaptureError(
                f"Capture completed but no image data for {camera.name}"
            )

        decoded = base64.b64decode(thumbnail.image)
        if len(decoded) < 2 or decoded[0] != 0xFF or decoded[1] != 0xD8:
            raise ImageCaptureError(
                f"Captured image is not valid JPEG for {camera.name}"
            )

        return decoded

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
                    if result.res == "ERROR":
                        raise OperationFailedError(
                            f"Panel rejected operation: {result.msg}",
                            error_code=None,
                            error_type=None,
                        )
                    if result.protom_response is not None:
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
