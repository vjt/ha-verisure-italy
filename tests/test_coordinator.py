"""Coordinator-level behaviour.

Covers the `suppress_updates()` race guard introduced for M3: while a
mutation (arm/disarm) is in flight, the poller must NOT hit the
Verisure client — otherwise a stale panel snapshot could overwrite the
ARMING/DISARMING transition state.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.verisure_italy.coordinator import (
    VerisureCoordinator,
    VerisureStatusData,
)
from verisure_italy.models import (
    PROTO_TO_STATE,
    AlarmState,
    Installation,
    InteriorMode,
    PerimeterMode,
    ProtoCode,
)


def _make_data() -> VerisureStatusData:
    return VerisureStatusData(
        alarm_state=AlarmState(
            interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF,
        ),
        proto_code=ProtoCode.DISARMED,
        timestamp="2026-01-01T00:00:00",
        exceptions=[],
    )


def _bare_coordinator() -> VerisureCoordinator:
    """Build a coordinator without going through HA's __init__.

    The suppress context is pure attribute state — no HA internals
    needed. Bypassing __init__ keeps the test fast and HA-independent.
    """
    coord = VerisureCoordinator.__new__(VerisureCoordinator)
    coord._updates_suppressed = False  # type: ignore[attr-defined]
    coord.data = _make_data()  # type: ignore[attr-defined]
    coord.client = MagicMock()
    coord.client.get_general_status = AsyncMock()
    coord._cameras_discovered = True  # skip camera discovery branch
    coord._services_discovered = True  # skip xSSrv discovery branch
    coord.active_services = frozenset()  # type: ignore[attr-defined]
    coord.installation = Installation(
        number="1234567", alias="Home", panel="SDVECU", type="home",
    )
    return coord


class TestSuppressUpdates:
    async def test_blocks_client_call_and_returns_cached(self):
        coord = _bare_coordinator()
        async with coord.suppress_updates():
            result = await coord._async_update_data()
        assert result is coord.data
        coord.client.get_general_status.assert_not_called()

    async def test_flag_resets_after_context_exit(self):
        coord = _bare_coordinator()
        async with coord.suppress_updates():
            assert coord._updates_suppressed is True
        assert coord._updates_suppressed is False

    async def test_flag_resets_on_exception(self):
        coord = _bare_coordinator()
        with pytest.raises(RuntimeError, match="boom"):
            async with coord.suppress_updates():
                raise RuntimeError("boom")
        assert coord._updates_suppressed is False

    async def test_reentrance_rejected(self):
        coord = _bare_coordinator()
        async with coord.suppress_updates():
            with pytest.raises(RuntimeError, match="not re-entrant"):
                async with coord.suppress_updates():
                    pass

    async def test_pre_first_refresh_rejected(self):
        coord = _bare_coordinator()
        coord.data = None
        with pytest.raises(RuntimeError, match="before first refresh"):
            async with coord.suppress_updates():
                pass


class TestPollShortCircuit:
    async def test_poll_outside_suppress_calls_client(self):
        """Sanity: outside suppress context, poll hits the client."""
        coord = _bare_coordinator()
        status = MagicMock()
        status.status = "D"
        status.timestamp_update = "2026-01-01T00:00:00"
        status.exceptions = None
        coord.client.get_general_status = AsyncMock(return_value=status)
        coord.client.set_last_proto = MagicMock()

        result = await coord._async_update_data()

        coord.client.get_general_status.assert_awaited_once()
        assert result.alarm_state == PROTO_TO_STATE[ProtoCode.DISARMED]
