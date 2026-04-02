"""Verisure IT API data models.

Pydantic models for all API request/response types. These are the
boundary — JSON dicts from the Verisure GraphQL API get parsed here.
If parsing fails, it blows up here. Inside the codebase, types
guarantee correctness.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

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
    """Parse a proto response code. Raises ValueError on unknown codes."""
    try:
        return ProtoCode(code)
    except ValueError:
        raise ValueError(
            f"Unknown proto response code {code!r}. "
            f"Valid codes: {', '.join(c.value for c in ProtoCode)}"
        ) from None


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

    number: str = Field(alias="numinst")
    alias: str
    panel: str
    type: str
    name: str
    surname: str
    address: str
    city: str
    postcode: str
    province: str
    email: str
    phone: str


class OperationResult(BaseModel):
    """Result of a check-alarm-status, arm-status, or disarm-status poll.

    This is the core response from any alarm state query. The protom_response
    field contains the ProtoCode that determines the alarm state.
    """

    res: str
    msg: str
    status: str | None
    numinst: str
    protom_response: str = Field(alias="protomResponse")
    protom_response_data: str = Field(alias="protomResponseDate")

    @property
    def proto_code(self) -> ProtoCode:
        """Parse protom_response into a ProtoCode. Raises ValueError if unknown."""
        return parse_proto_code(self.protom_response)

    @property
    def alarm_state(self) -> AlarmState:
        """Resolve to an AlarmState. Raises ValueError if proto code is unknown."""
        return PROTO_TO_STATE[self.proto_code]

    @property
    def timestamp(self) -> datetime:
        """Parse the response timestamp. Raises ValueError on bad format."""
        return datetime.fromisoformat(self.protom_response_data)

    @property
    def is_pending(self) -> bool:
        """True if the operation is still in progress."""
        return self.res == "WAIT"


class ArmResult(BaseModel):
    """Result of an arm operation status poll."""

    res: str
    msg: str
    status: str | None
    numinst: str
    protom_response: str = Field(alias="protomResponse")
    protom_response_data: str = Field(alias="protomResponseDate")
    request_id: str = Field(alias="requestId")
    error: dict[str, object] | None

    @property
    def proto_code(self) -> ProtoCode:
        return parse_proto_code(self.protom_response)

    @property
    def alarm_state(self) -> AlarmState:
        return PROTO_TO_STATE[self.proto_code]

    @property
    def is_pending(self) -> bool:
        return self.res == "WAIT"


class DisarmResult(BaseModel):
    """Result of a disarm operation status poll."""

    res: str
    msg: str
    numinst: str
    protom_response: str = Field(alias="protomResponse")
    protom_response_data: str = Field(alias="protomResponseDate")
    request_id: str = Field(alias="requestId")
    error: dict[str, object] | None

    @property
    def proto_code(self) -> ProtoCode:
        return parse_proto_code(self.protom_response)

    @property
    def alarm_state(self) -> AlarmState:
        return PROTO_TO_STATE[self.proto_code]

    @property
    def is_pending(self) -> bool:
        return self.res == "WAIT"


class GeneralStatus(BaseModel):
    """Result of xSStatus — passive status query that doesn't ping the panel."""

    status: str
    timestamp_update: str = Field(alias="timestampUpdate")


class CheckAlarmResponse(BaseModel):
    """Response from xSCheckAlarm — initiates a status check on the panel."""

    res: str
    msg: str
    reference_id: str = Field(alias="referenceId")


class ArmPanelResponse(BaseModel):
    """Response from xSArmPanel — initiates an arm operation."""

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


class Service(BaseModel):
    """A service available on the installation."""

    id_service: int = Field(alias="idService")
    active: bool
    visible: bool
    request: str
    description: str | None = None
