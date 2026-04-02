"""Typed response envelopes for every Verisure GraphQL operation.

Each envelope describes the EXACT shape of the JSON response from the API.
Pydantic parses the raw JSON string directly into these models.
If the response doesn't match: ValidationError. No dicts, no Any, no negotiation.
"""

from pydantic import BaseModel, Field

from .models import (
    ArmResult,
    CheckAlarmResponse,
    DisarmPanelResponse,
    DisarmResult,
    GeneralStatus,
    Installation,
    LoginResponse,
    OperationResult,
    OtpPhone,
    Service,
)

# --- Nested wrappers that mirror the GraphQL response structure ---


class _InstallationList(BaseModel):
    installations: list[Installation]


class _ServiceInstallation(BaseModel):
    numinst: str
    capabilities: str
    services: list[Service]


class _SrvData(BaseModel):
    res: str
    msg: str
    installation: _ServiceInstallation


class _OtpResult(BaseModel):
    res: str
    msg: str | None


# --- Top-level response envelopes (one per GraphQL operation) ---


class LoginEnvelope(BaseModel):
    """Response from mkLoginToken."""

    class Data(BaseModel):
        xSLoginToken: LoginResponse  # noqa: N815

    data: Data


class ValidateDeviceEnvelope(BaseModel):
    """Response from mkValidateDevice."""

    class ValidateResult(BaseModel):
        res: str
        msg: str | None
        hash: str | None
        refresh_token: str | None = Field(alias="refreshToken")

    class Data(BaseModel):
        xSValidateDevice: "ValidateDeviceEnvelope.ValidateResult"  # noqa: N815

    data: Data


class SendOtpEnvelope(BaseModel):
    """Response from mkSendOTP."""

    class Data(BaseModel):
        xSSendOtp: _OtpResult  # noqa: N815

    data: Data


class InstallationListEnvelope(BaseModel):
    """Response from mkInstallationList."""

    class Data(BaseModel):
        xSInstallations: _InstallationList  # noqa: N815

    data: Data


class ServicesEnvelope(BaseModel):
    """Response from Srv."""

    class Data(BaseModel):
        xSSrv: _SrvData  # noqa: N815

    data: Data


class CheckAlarmEnvelope(BaseModel):
    """Response from CheckAlarm."""

    class Data(BaseModel):
        xSCheckAlarm: CheckAlarmResponse  # noqa: N815

    data: Data


class CheckAlarmStatusEnvelope(BaseModel):
    """Response from CheckAlarmStatus."""

    class Data(BaseModel):
        xSCheckAlarmStatus: OperationResult  # noqa: N815

    data: Data


class GeneralStatusEnvelope(BaseModel):
    """Response from Status (xSStatus)."""

    class Data(BaseModel):
        xSStatus: GeneralStatus  # noqa: N815

    data: Data


class ArmPanelEnvelope(BaseModel):
    """Response from xSArmPanel."""

    class ArmPanelResult(BaseModel):
        res: str
        msg: str
        reference_id: str = Field(alias="referenceId")

    class Data(BaseModel):
        xSArmPanel: "ArmPanelEnvelope.ArmPanelResult"  # noqa: N815

    data: Data


class ArmStatusEnvelope(BaseModel):
    """Response from ArmStatus."""

    class Data(BaseModel):
        xSArmStatus: ArmResult  # noqa: N815

    data: Data


class DisarmPanelEnvelope(BaseModel):
    """Response from xSDisarmPanel."""

    class Data(BaseModel):
        xSDisarmPanel: DisarmPanelResponse  # noqa: N815

    data: Data


class DisarmStatusEnvelope(BaseModel):
    """Response from DisarmStatus."""

    class Data(BaseModel):
        xSDisarmStatus: DisarmResult  # noqa: N815

    data: Data


# --- Error response structures ---


class GraphQLErrorData(BaseModel):
    """Data field inside a GraphQL error object."""

    reason: str | None = None
    status: int | None = None
    need_device_authorization: bool | None = Field(
        None, alias="needDeviceAuthorization"
    )
    auth_otp_hash: str | None = Field(None, alias="auth-otp-hash")
    auth_phones: list[OtpPhone] | None = Field(None, alias="auth-phones")


class GraphQLError(BaseModel):
    """A single GraphQL error from the API response."""

    message: str = ""
    data: GraphQLErrorData | None = None


class ErrorResponse(BaseModel):
    """A response that contains GraphQL errors."""

    errors: list[GraphQLError]
