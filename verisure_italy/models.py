"""Verisure IT API data models.

Pydantic models for all API request/response types. These are the
boundary — JSON dicts from the Verisure GraphQL API get parsed here.
If parsing fails, it blows up here. Inside the codebase, types
guarantee correctness.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from .exceptions import UnexpectedStateError

# ---------------------------------------------------------------------------
# Alarm state model: two axes, six valid states
# ---------------------------------------------------------------------------


class InteriorMode(StrEnum):
    """Interior alarm mode.

    OFF: all interior sensors disabled.
    PARTIAL: shock/vibration sensors only (door/window impact).
    TOTAL: shock sensors + volumetric/PIR interior sensors.
    """

    OFF = "off"
    PARTIAL = "partial"
    TOTAL = "total"


class PerimeterMode(StrEnum):
    """Perimeter alarm mode."""

    OFF = "off"
    ON = "on"


class ProtoCode(StrEnum):
    """Protocol response codes from the Verisure panel.

    Each code maps to exactly one (InteriorMode, PerimeterMode) pair.
    Unknown codes MUST raise an error — never silently default.
    """

    DISARMED = "D"
    PERIMETER_ONLY = "E"
    PARTIAL = "P"
    PARTIAL_PERIMETER = "B"
    TOTAL = "T"
    TOTAL_PERIMETER = "A"


class AlarmState(BaseModel):
    """Two-axis alarm state. Immutable value object."""

    model_config = {"frozen": True}

    interior: InteriorMode
    perimeter: PerimeterMode

    def __hash__(self) -> int:
        return hash((self.interior, self.perimeter))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AlarmState):
            return NotImplemented
        return self.interior == other.interior and self.perimeter == other.perimeter


# The canonical mapping. Six entries, no defaults, no fallbacks.
PROTO_TO_STATE: dict[ProtoCode, AlarmState] = {
    ProtoCode.DISARMED: AlarmState(interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF),
    ProtoCode.PERIMETER_ONLY: AlarmState(
        interior=InteriorMode.OFF, perimeter=PerimeterMode.ON
    ),
    ProtoCode.PARTIAL: AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.OFF),
    ProtoCode.PARTIAL_PERIMETER: AlarmState(
        interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON
    ),
    ProtoCode.TOTAL: AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.OFF),
    ProtoCode.TOTAL_PERIMETER: AlarmState(
        interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON
    ),
}

STATE_TO_PROTO: dict[AlarmState, ProtoCode] = {v: k for k, v in PROTO_TO_STATE.items()}


def parse_proto_code(code: str) -> ProtoCode:
    """Parse a proto response code. Raises UnexpectedStateError on unknown codes."""
    try:
        return ProtoCode(code)
    except ValueError:
        raise UnexpectedStateError(code) from None


# ---------------------------------------------------------------------------
# API arm/disarm commands
# ---------------------------------------------------------------------------


class ArmCommand(StrEnum):
    """API command strings for arming/disarming.

    These are the values sent in the `request` field of xSArmPanel
    and xSDisarmPanel GraphQL mutations.
    """

    DISARM = "DARM1"
    ARM_PARTIAL = "ARMDAY1"
    ARM_TOTAL = "ARM1"
    ARM_PERIMETER = "PERI1"
    ARM_TOTAL_PERIMETER = "ARM1PERI1"
    ARM_PARTIAL_PERIMETER = "ARMDAY1PERI1"
    DISARM_ALL = "DARM1DARMPERI"


# Map each target AlarmState to the command that reaches it.
# Only states we can actively transition TO are mapped here.
STATE_TO_COMMAND: dict[AlarmState, ArmCommand] = {
    PROTO_TO_STATE[ProtoCode.DISARMED]: ArmCommand.DISARM_ALL,
    PROTO_TO_STATE[ProtoCode.PERIMETER_ONLY]: ArmCommand.ARM_PERIMETER,
    PROTO_TO_STATE[ProtoCode.PARTIAL]: ArmCommand.ARM_PARTIAL,
    PROTO_TO_STATE[ProtoCode.PARTIAL_PERIMETER]: ArmCommand.ARM_PARTIAL_PERIMETER,
    PROTO_TO_STATE[ProtoCode.TOTAL]: ArmCommand.ARM_TOTAL,
    PROTO_TO_STATE[ProtoCode.TOTAL_PERIMETER]: ArmCommand.ARM_TOTAL_PERIMETER,
}


# ---------------------------------------------------------------------------
# API response models — parsed at the boundary
# ---------------------------------------------------------------------------


class Installation(BaseModel):
    """A Verisure installation (premises)."""

    model_config = {"populate_by_name": True}

    # Load-bearing — code uses these for API calls + UI.
    number: str = Field(alias="numinst")
    alias: str
    panel: str
    # Metadata — never read by code, soften so Verisure schema drift
    # (any of these arriving as null) doesn't crash setup. Issue #2.
    type: str | None = None
    name: str | None = None
    surname: str | None = None
    address: str | None = None
    city: str | None = None
    postcode: str | None = None
    province: str | None = None
    email: str | None = None
    phone: str | None = None


class _AlarmOperationBase(BaseModel):
    """Shared base for alarm operation results.

    Provides proto_code, alarm_state, and is_pending from the common
    protom_response and res fields. Raises UnexpectedStateError on
    unknown proto codes, never defaults.
    """

    res: str
    msg: str | None
    status: str | None = None
    numinst: str | None
    protom_response: str | None = Field(alias="protomResponse")
    protom_response_data: str | None = Field(alias="protomResponseDate")

    @property
    def proto_code(self) -> ProtoCode:
        """Parse protom_response into a ProtoCode. Raises if unknown or pending."""
        if self.protom_response is None:
            raise ValueError("No proto code — operation still pending")
        return parse_proto_code(self.protom_response)

    @property
    def alarm_state(self) -> AlarmState:
        """Resolve to an AlarmState. Raises if proto code is unknown or pending."""
        return PROTO_TO_STATE[self.proto_code]

    @property
    def is_pending(self) -> bool:
        """True if the operation is still in progress."""
        return self.res == "WAIT"


class OperationResult(_AlarmOperationBase):
    """Result of a check-alarm-status poll.

    During WAIT (pending), most fields are null — only access proto_code,
    alarm_state, timestamp on completed results.
    """

    @property
    def timestamp(self) -> datetime:
        """Parse the response timestamp. Raises ValueError on bad format or pending."""
        if self.protom_response_data is None:
            raise ValueError("No timestamp — operation still pending")
        return datetime.fromisoformat(self.protom_response_data)


class ZoneException(BaseModel):
    """An open zone reported during arming (from xSGetExceptions)."""

    status: str
    device_type: str = Field(alias="deviceType")
    alias: str


class PanelError(BaseModel):
    """Error details from the panel (returned in arm/disarm status).

    code and type are always present on panel errors. The remaining
    fields are only populated for NON_BLOCKING (force-arm-eligible)
    errors.
    """

    code: str
    type: str
    allow_forcing: bool | None = Field(None, alias="allowForcing")
    exceptions_number: int | None = Field(None, alias="exceptionsNumber")
    reference_id: str | None = Field(None, alias="referenceId")
    suid: str | None = None


class ArmResult(_AlarmOperationBase):
    """Result of an arm operation status poll."""

    request_id: str | None = Field(alias="requestId")
    error: PanelError | None


class DisarmResult(_AlarmOperationBase):
    """Result of a disarm operation status poll."""

    request_id: str | None = Field(alias="requestId")
    error: PanelError | None


class GeneralStatus(BaseModel):
    """Result of xSStatus — passive status query that doesn't ping the panel."""

    status: str
    timestamp_update: str = Field(alias="timestampUpdate")
    exceptions: list[ZoneException] | None = None


class CheckAlarmResponse(BaseModel):
    """Response from xSCheckAlarm — initiates a status check on the panel."""

    res: str
    msg: str
    reference_id: str = Field(alias="referenceId")


class DisarmPanelResponse(BaseModel):
    """Response from xSDisarmPanel — initiates a disarm operation."""

    res: str
    msg: str
    reference_id: str = Field(alias="referenceId")


class LoginResponse(BaseModel):
    """Response from xSLoginToken."""

    res: str
    msg: str
    hash: str | None
    refresh_token: str | None = Field(alias="refreshToken")
    need_device_authorization: bool | None = Field(alias="needDeviceAuthorization")


class OtpPhone(BaseModel):
    """Phone number available for OTP challenge."""

    id: int
    phone: str


class ServiceAttribute(BaseModel):
    """A single attribute inside a Service's attributes list.

    Verisure attributes carry per-panel capability details (limits,
    secondary commands, labels). Names and values vary by panel type —
    parsed structurally, interpreted downstream.
    """

    model_config = {"populate_by_name": True}

    name: str | None = None
    value: str | None = None
    active: bool | None = None


class _ServiceAttributesWrapper(BaseModel):
    """Inner wrapper for Service.attributes — GraphQL returns attributes.attributes."""

    attributes: list[ServiceAttribute] | None = None


class Service(BaseModel):
    """A service available on the installation.

    `attributes` carries per-panel capability data — load-bearing for
    panel-type discovery.
    """

    model_config = {"populate_by_name": True}

    id_service: int = Field(alias="idService")
    active: bool
    visible: bool
    request: str
    description: str | None = None
    bde: bool | None = None
    is_premium: bool | None = Field(None, alias="isPremium")
    cod_oper: bool | None = Field(None, alias="codOper")
    min_wrapper_version: str | None = Field(None, alias="minWrapperVersion")
    attributes: _ServiceAttributesWrapper | None = None


# Panel types for which explicit command maps have been verified on live
# hardware. Arm/disarm commands are sent ONLY to these panels. Unknown
# panels raise UnsupportedPanelError — fail-secure, no blind commands.
SUPPORTED_PANELS: frozenset[str] = frozenset({"SDVECU"})


# ---------------------------------------------------------------------------
# Camera models
# ---------------------------------------------------------------------------

# Device types that represent cameras in xSDeviceList
CAMERA_DEVICE_TYPES: frozenset[str] = frozenset({"QR", "YR", "YP", "QP"})

# Maps camera device type to the deviceType parameter for xSRequestImages
CAMERA_IMAGE_DEVICE_TYPE: dict[str, int] = {
    "QR": 106,
    "YR": 106,
    "YP": 103,
    "QP": 107,
}

# Resolution 0 = default/auto, Media type 1 = JPEG (from upstream API analysis)
CAMERA_IMAGE_RESOLUTION = 0
CAMERA_IMAGE_MEDIA_TYPE = 1


class RawDevice(BaseModel):
    """Raw device from xSDeviceList — parsed at boundary, filtered by client."""

    model_config = {"populate_by_name": True}

    id: str
    code: str
    zone_id: str | None = Field(None, alias="zoneId")
    name: str
    device_type: str = Field(alias="type")
    is_active: bool | None = Field(alias="isActive")
    serial_number: str | None = Field(None, alias="serialNumber")


class CameraDevice(BaseModel):
    """A camera device, filtered and cleaned from xSDeviceList."""

    model_config = {"frozen": True}

    id: str
    code: int
    zone_id: str
    name: str
    device_type: str
    serial_number: str | None = None


class Thumbnail(BaseModel):
    """Response from xSGetThumbnail — latest captured image for a camera."""

    model_config = {"populate_by_name": True}

    id_signal: str | None = Field(None, alias="idSignal")
    device_id: str | None = Field(None, alias="deviceId")
    device_code: str | None = Field(None, alias="deviceCode")
    device_alias: str | None = Field(None, alias="deviceAlias")
    timestamp: str | None = None
    signal_type: str | None = Field(None, alias="signalType")
    image: str | None = None  # base64-encoded JPEG
    type: str | None = None
    quality: str | None = None


class PhotoImage(BaseModel):
    """A single image from xSGetPhotoImages."""

    id: str
    image: str  # base64-encoded
    type: str  # "BINARY" for actual JPEG data


class PhotoDevice(BaseModel):
    """Device entry from xSGetPhotoImages response."""

    model_config = {"populate_by_name": True}

    id: str
    id_signal: str = Field(alias="idSignal")
    code: str
    name: str
    quality: str | None = None
    images: list[PhotoImage]


class RequestImagesResult(BaseModel):
    """Response from xSRequestImages — initiates image capture."""

    model_config = {"populate_by_name": True}

    res: str
    msg: str | None = None
    reference_id: str = Field(alias="referenceId")


class RequestImagesStatusResult(BaseModel):
    """Response from xSRequestImagesStatus — capture progress."""

    res: str
    msg: str | None = None
    numinst: str | None = None
    status: str | None = None
