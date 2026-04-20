"""Read-only diagnostic probe for a Verisure installation.

`run_probe(client, installation)` dumps every field the Verisure
GraphQL API declares about a panel's capabilities — services (with
their per-panel attributes), device list, and current alarm state.
Output is used to figure out what commands an unknown panel accepts
without sending anything to it.

**Strictly read-only.** The probe only issues queries that the mobile
app runs passively on startup:
  - xSInstallations (indirectly, via the caller supplying the model)
  - xSSrv          (service list + attributes + capabilities JWT)
  - xSDeviceList   (raw device dump)
  - xSStatus       (server-cached alarm status, no panel ping)

No arm/disarm, no xSCheckAlarm (which IS a panel ping and shows up
in the timeline).

PII is redacted at the boundary. `numinst` becomes an 8-char sha256
prefix; names, addresses, phone numbers, device serials, JWT tokens,
and reference IDs are dropped entirely. What remains is structural
capability data — panel code, service IDs, request strings, and any
attributes the API declares.

The redaction function is pure and unit-tested. A unit test asserts
every sensitive field is absent from a fixture-generated probe.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import VerisureClient
    from .models import Installation

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
