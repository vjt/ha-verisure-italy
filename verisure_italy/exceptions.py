"""Verisure API exceptions.

Every exception type corresponds to a distinct failure mode.
No generic catch-alls. Callers handle specific errors or let them
propagate to generate human-visible notifications.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import ArmCommand, ServiceRequest, ZoneException


class VerisureError(Exception):
    """Base exception for all Verisure API errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AuthenticationError(VerisureError):
    """Login failed — bad credentials or account locked."""


class TwoFactorRequiredError(VerisureError):
    """Device needs 2FA validation before it can be used."""


class SessionExpiredError(VerisureError):
    """JWT token expired — need to re-authenticate."""


class APIResponseError(VerisureError):
    """API returned an error in the GraphQL response.

    Attributes:
        http_status: HTTP status code if available.
    """

    def __init__(
        self,
        message: str,
        http_status: int | None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status


class WAFBlockedError(VerisureError):
    """Request blocked by Incapsula WAF. Back off and retry later."""


class APIConnectionError(VerisureError):
    """Network-level failure connecting to the Verisure API."""


class UnexpectedStateError(VerisureError):
    """Alarm reported a state that doesn't match our model.

    This is a security-critical error. The integration MUST NOT
    guess or default — it must notify a human.
    """

    def __init__(self, proto_code: str) -> None:
        super().__init__(
            f"Unexpected alarm proto code: {proto_code!r}. "
            f"Human verification required."
        )
        self.proto_code = proto_code


class StateNotObservedError(VerisureError):
    """No xSStatus observation yet — arm/disarm requires a known current state.

    The resolver selects armed→armed transition commands (ARMINTFPART1,
    ARMPARTFINTDAY1, ARMPARTFINTNIGHT1) based on the CURRENT alarm
    state. Until the client has seen at least one xSStatus response,
    we don't know the current state and cannot safely pick a command.

    Usually transient: the HA coordinator polls xSStatus on setup
    before any arm/disarm can be dispatched.
    """


class OperationTimeoutError(VerisureError):
    """Arm/disarm operation did not complete within the timeout.

    Fail-secure: caller must assume the previous state is still
    active and notify a human.
    """


class OperationFailedError(VerisureError):
    """Arm/disarm operation was rejected by the panel.

    Attributes:
        error_code: panel error code if available.
        error_type: panel error type if available.
    """

    def __init__(
        self,
        message: str,
        error_code: str | None,
        error_type: str | None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.error_type = error_type


class UnsupportedPanelError(VerisureError):
    """Panel type is not in SUPPORTED_PANELS — arm/disarm refused.

    Fail-secure: the integration has no verified command mapping for
    this panel, so it sends nothing. Caller must surface a probe so the
    user can report capabilities and get the panel type supported.
    """

    def __init__(self, panel: str, message: str) -> None:
        super().__init__(message)
        self.panel = panel


class ImageCaptureError(VerisureError):
    """Image capture failed — timeout, invalid data, or no image returned."""


class ArmingExceptionError(VerisureError):
    """Arming blocked by open zones (NON_BLOCKING with allowForcing).

    Carries force-arm context so the caller can retry with
    forceArmingRemoteId to override the exception.
    """

    def __init__(
        self,
        reference_id: str,
        suid: str,
        exceptions: list[ZoneException],
    ) -> None:
        details = ", ".join(e.alias for e in exceptions)
        super().__init__(f"Arming blocked by open zones: {details}")
        self.reference_id = reference_id
        self.suid = suid
        self.exceptions = exceptions


class UnsupportedCommandError(VerisureError):
    """The panel's active services don't support the requested command.

    Raised by CommandResolver before any mutation is sent. The command
    would be rejected by the panel anyway — we surface the refusal
    locally with diagnostic context (which services are missing)
    instead of paying a round-trip + deciphering the API error.
    """

    def __init__(
        self,
        *,
        command: ArmCommand,
        panel: str,
        missing_services: frozenset[ServiceRequest],
    ) -> None:
        self.command = command
        self.panel = panel
        self.missing_services = missing_services
        missing = ", ".join(sorted(s.value for s in missing_services))
        super().__init__(
            f"Panel {panel!r} cannot honour {command.value!r}: "
            f"missing active service(s): {missing}"
        )
