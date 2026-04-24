"""Read-only diagnostics for a Verisure installation.

Two responsibilities, one module, shared PII-redaction guarantees:

1. `run_probe(client, installation)` — dumps every field the Verisure
   GraphQL API declares about a panel's capabilities (services +
   attributes + devices + current alarm status). Async; issues only
   read-only queries (xSSrv, xSDeviceList, xSStatus) — nothing that
   pings the panel or shows up in the timeline. Output is intended
   for copy-paste into GitHub issues when adding support for an
   unknown panel type.

2. `format_failure_report(...)` — pure sync formatter over pre-parsed
   types. Produces a BEGIN/END-marker-wrapped block summarising an
   arm/disarm failure for a single ERROR log line. Same cut-marker
   convention as run_probe so users can copy-paste the block verbatim
   into a bug report.

Both outputs are PII-safe by construction. `numinst` is replaced by
an 8-char sha256 prefix; names, addresses, phone numbers, device
serials, JWT tokens, and reference IDs are never included. The
`_PII_FIELDS` set + `assert_redacted()` act as belt-and-braces for
the probe path; the failure report redacts structurally by only
serialising a fixed, whitelisted set of fields.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .models import PANEL_FAMILIES, ArmCommand, Installation, ServiceRequest

if TYPE_CHECKING:
    from .client import VerisureClient
    from .exceptions import VerisureError

_LOGGER = logging.getLogger(__name__)

PROBE_SCHEMA_VERSION = 1

# Probe values are JSON-shaped: primitives, lists, and nested dicts.
# Recursive type alias — declared via PEP 695 `type` syntax (Python 3.12+).
type ProbeValue = (
    str | int | float | bool | None | list["ProbeValue"] | dict[str, "ProbeValue"]
)
type ProbeDict = dict[str, ProbeValue]

# Field names whose presence anywhere in the probe output is a
# redaction bug. `name` / `alias` are deliberately NOT listed here — they
# appear legitimately inside service attribute dicts as capability
# descriptors (`"name": "MODE_ARM"`). PII-flavoured `name` (installation
# owner, device labels) is dropped structurally during probe construction.
# The `test_no_pii_values_in_serialized_output` test catches value leakage.
_PII_FIELDS: frozenset[str] = frozenset({
    "numinst", "phone", "email", "surname", "address", "city",
    "postcode", "province", "serialNumber", "serial_number",
    "auth_token", "capabilities", "referenceId", "reference_id",
    "refreshToken", "refresh_token", "hash", "idDevice", "idDeviceIndigitall",
    "uuid",
})


def _hash_numinst(numinst: str) -> str:
    """Return an 8-char sha256 prefix of the installation number.

    Stable across reports (same numinst -> same hash) but not reversible
    to a real installation ID.
    """
    return hashlib.sha256(numinst.encode("utf-8")).hexdigest()[:8]


async def run_probe(
    client: VerisureClient, installation: Installation
) -> ProbeDict:
    """Collect read-only diagnostic data for a Verisure installation.

    Returns a redacted dict conforming to PROBE_SCHEMA_VERSION. Never
    sends any panel-affecting command. May raise the same exceptions
    as the underlying client calls (APIResponseError, session errors,
    etc.) — probe failure is as loud as normal operation failure.
    """
    _LOGGER.debug(
        "probe: starting for panel=%s numinst=%s",
        installation.panel, _hash_numinst(installation.number),
    )

    services = await client.get_services(installation)
    devices = await client.get_raw_device_list(installation)
    status = await client.get_general_status(installation)

    from . import __version__ as client_version

    service_entries: list[ProbeValue] = [
        {
            "idService": svc.id_service,
            "active": svc.active,
            "visible": svc.visible,
            "request": svc.request,
            "bde": svc.bde,
            "isPremium": svc.is_premium,
            "codOper": svc.cod_oper,
            "description": svc.description,
            "minWrapperVersion": svc.min_wrapper_version,
            "attributes": (
                [
                    {
                        "name": attr.name,
                        "value": attr.value,
                        "active": attr.active,
                    }
                    for attr in svc.attributes.attributes
                ]
                if svc.attributes is not None
                and svc.attributes.attributes is not None
                else []
            ),
        }
        for svc in services
    ]
    device_entries: list[ProbeValue] = [
        {
            "id": dev.id,
            "code": dev.code,
            "zoneId": dev.zone_id,
            "type": dev.device_type,
            "idService": None,  # not exposed in current GraphQL query
            "isActive": dev.is_active,
        }
        for dev in devices
    ]
    exception_entries: list[ProbeValue] = [
        {
            "status": exc.status,
            "deviceType": exc.device_type,
            # alias may carry user-chosen zone names — drop it
        }
        for exc in (status.exceptions or [])
    ]

    probe: ProbeDict = {
        "schema_version": PROBE_SCHEMA_VERSION,
        "timestamp": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "client_version": client_version,
        "installation": {
            "panel": installation.panel,
            "type": installation.type,
            "numinst_hash": _hash_numinst(installation.number),
        },
        "services": service_entries,
        "devices": device_entries,
        "alarm_state": {
            "status": status.status,
            "timestampUpdate": status.timestamp_update,
            "exceptions": exception_entries,
        },
    }

    _LOGGER.debug(
        "probe: collected %d services, %d devices",
        len(service_entries), len(device_entries),
    )
    return probe


def assert_redacted(probe: ProbeDict) -> None:
    """Walk a probe dict and raise ValueError if any PII field is present.

    Defence in depth: the probe function is supposed to build a redacted
    structure from the start. This function verifies no PII leaked, used
    by tests and as a belt-and-braces check before logging/emitting.
    """

    def _walk(node: ProbeValue, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _PII_FIELDS:
                    raise ValueError(
                        f"PII field {key!r} present at {path or '<root>'}"
                    )
                _walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    _walk(probe, "")


# ---------------------------------------------------------------------------
# Failure report — pure sync formatter, fixed whitelist of fields, PII-safe.
# ---------------------------------------------------------------------------


def _client_version() -> str:
    """Read the client version lazily to avoid module-init import cycles."""
    from . import __version__
    return __version__


def format_failure_report(
    *,
    operation: str,
    installation: Installation,
    command: ArmCommand | None,
    active_services: frozenset[ServiceRequest],
    current_proto: str,
    error: VerisureError,
) -> str:
    """Format a structured failure report wrapped in BEGIN/END cut markers.

    Intended for a single ERROR-level log on arm/disarm failure paths. The
    marker convention matches `run_probe` output — users can copy the block
    verbatim into a GitHub issue. All fields are PII-safe: panel code +
    family + services + proto + command + error details only.

    `operation` must be one of "arm" / "disarm". `command` may be None if
    the failure occurred before the resolver picked a command. `current_proto`
    may be "" if no state has been observed.

    Returns the formatted multi-line string. Does no IO.
    """
    op = operation.upper()
    begin = f"=== VERISURE {op} FAILURE BEGIN ==="
    end = f"=== VERISURE {op} FAILURE END ==="

    family = PANEL_FAMILIES.get(installation.panel)
    family_str = family.value.upper() if family is not None else "UNKNOWN"

    services_sorted = ", ".join(sorted(s.value for s in active_services))

    command_str = command.value if command is not None else "N/A"

    # Where possible pull error_code off OperationFailedError etc.
    error_code = getattr(error, "error_code", None)
    error_code_str = repr(error_code) if error_code is not None else "null"

    lines = [
        begin,
        f"client_version: {_client_version()}",
        f"timestamp: {datetime.now(tz=UTC).isoformat()}",
        f"panel: {installation.panel}",
        f"family: {family_str}",
        f"numinst_hash: {_hash_numinst(installation.number)}",
        f"current_proto: {current_proto!r}",
        f"command_selected: {command_str}",
        f"active_services: [{services_sorted}]",
        f"error_type: {type(error).__name__}",
        f"error_code: {error_code_str}",
        f"error_message: {error.message}",
        end,
    ]
    return "\n".join(lines)
