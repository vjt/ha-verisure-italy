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
from verisure_italy.client import VerisureClient
from verisure_italy.models import (
    PROTO_TO_STATE,
    AlarmState,
    GeneralStatus,
    Installation,
    InteriorMode,
    PerimeterMode,
    ProtoCode,
)


def _make_data() -> VerisureStatusData:
    return VerisureStatusData(
        alarm_state=AlarmState(
            interior=InteriorMode.OFF,
            perimeter=PerimeterMode.OFF,
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
    coord.client.observed_proto = MagicMock(return_value=None)
    coord._pending_disarm = None
    coord._disarm_recheck_unsub = None
    coord._cameras_discovered = True  # skip camera discovery branch
    coord._services_discovered = True  # skip xSSrv discovery branch
    coord.active_services = frozenset()  # type: ignore[attr-defined]
    coord.installation = Installation(
        number="1234567",
        alias="Home",
        panel="SDVECU",
        type="home",
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


# ---------------------------------------------------------------------------
# Disarm confirmation (phantom "D", docs/findings/phantom-disarm.md)
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _status(code: str, timestamp_update: str) -> GeneralStatus:
    return GeneralStatus.model_validate(
        {"status": code, "timestampUpdate": timestamp_update, "exceptions": None}
    )


def _snapshot(code: ProtoCode) -> VerisureStatusData:
    return VerisureStatusData(
        alarm_state=PROTO_TO_STATE[code],
        proto_code=code,
        timestamp="1792252634012",
        exceptions=[],
    )


@pytest.fixture
def scheduled(monkeypatch: pytest.MonkeyPatch) -> list[MagicMock]:
    """Record async_call_later calls; each returns a MagicMock unsub."""
    calls: list[MagicMock] = []

    def _fake_call_later(_hass: object, _delay: float, action: object) -> MagicMock:
        unsub = MagicMock()
        unsub.action = action
        unsub.delay = _delay
        calls.append(unsub)
        return unsub

    monkeypatch.setattr(
        "custom_components.verisure_italy.coordinator.async_call_later", _fake_call_later
    )
    return calls


def _confirming_coordinator(prior: ProtoCode | None) -> tuple[VerisureCoordinator, _FakeClock]:
    """Coordinator with a real client whose last observation is `prior`."""
    coord = _bare_coordinator()
    coord.client = VerisureClient(
        username="test@test.it",
        password="password123",
        http_session=MagicMock(),
        device_id="test-device-id",
        uuid="test-uuid-1234",
        id_device_indigitall="",
        poll_delay=0.0,
        poll_timeout=2.0,
    )
    if prior is None:
        coord.data = None
    else:
        coord.client.set_last_proto(prior.value)
        coord.data = _snapshot(prior)
    coord.hass = MagicMock()
    clock = _FakeClock()
    coord._clock = clock
    coord.disarm_confirm_delay = 5.0
    coord._pending_disarm = None
    coord._disarm_recheck_unsub = None
    return coord, clock


async def _poll(
    coord: VerisureCoordinator, code: str, timestamp_update: str
) -> VerisureStatusData:
    coord.client.get_general_status = AsyncMock(return_value=_status(code, timestamp_update))
    return await coord._async_update_data()


class TestDisarmConfirmation:
    async def test_phantom_d_never_published(self, scheduled):
        """21:07 incident: D for 4.3s, then A with a newer timestampUpdate."""
        coord, clock = _confirming_coordinator(ProtoCode.TOTAL_PERIMETER)
        armed = coord.data

        held = await _poll(coord, "D", "1792264029172")
        assert held is armed
        assert coord.client.observed_proto() == ProtoCode.TOTAL_PERIMETER

        clock.now += 4.3
        result = await _poll(coord, "A", "1792264033514")
        assert result.alarm_state == PROTO_TO_STATE[ProtoCode.TOTAL_PERIMETER]
        assert coord._pending_disarm is None
        scheduled[0].assert_called_once()  # recheck cancelled

    async def test_real_disarm_published_after_delay(self, scheduled):
        """21:40 capture: D repeated with the same timestampUpdate."""
        coord, clock = _confirming_coordinator(ProtoCode.TOTAL_PERIMETER)

        assert await _poll(coord, "D", "1792266009978") is coord.data
        assert len(scheduled) == 1
        assert scheduled[0].delay == 5.0

        clock.now += 5.0
        result = await _poll(coord, "D", "1792266009978")
        assert result.alarm_state == PROTO_TO_STATE[ProtoCode.DISARMED]
        assert coord.client.observed_proto() == ProtoCode.DISARMED
        assert coord._pending_disarm is None

    async def test_same_d_before_delay_still_held(self, scheduled):
        coord, clock = _confirming_coordinator(ProtoCode.TOTAL_PERIMETER)
        armed = coord.data

        await _poll(coord, "D", "1792266009978")
        clock.now += 3.0
        assert await _poll(coord, "D", "1792266009978") is armed

    async def test_new_timestamp_restarts_confirmation(self, scheduled):
        coord, clock = _confirming_coordinator(ProtoCode.TOTAL_PERIMETER)
        armed = coord.data

        await _poll(coord, "D", "1792266009978")
        clock.now += 3.0
        assert await _poll(coord, "D", "1792266012978") is armed
        clock.now += 3.0  # 6s after the first D, 3s after the new one
        assert await _poll(coord, "D", "1792266012978") is armed
        clock.now += 2.0
        result = await _poll(coord, "D", "1792266012978")
        assert result.alarm_state == PROTO_TO_STATE[ProtoCode.DISARMED]

    async def test_own_disarm_published_immediately(self, scheduled):
        """After our disarm command the client already tracks D."""
        coord, _clock = _confirming_coordinator(ProtoCode.TOTAL_PERIMETER)
        coord.client.set_last_proto(ProtoCode.DISARMED.value)

        result = await _poll(coord, "D", "1792266009978")

        assert result.alarm_state == PROTO_TO_STATE[ProtoCode.DISARMED]
        assert scheduled == []

    async def test_first_refresh_d_published_immediately(self, scheduled):
        coord, _clock = _confirming_coordinator(None)

        result = await _poll(coord, "D", "1792266009978")

        assert result.alarm_state == PROTO_TO_STATE[ProtoCode.DISARMED]
        assert scheduled == []

    async def test_recheck_triggers_refresh(self, scheduled):
        coord, _clock = _confirming_coordinator(ProtoCode.TOTAL_PERIMETER)
        coord.async_refresh = MagicMock(return_value="refresh-coro")

        await _poll(coord, "D", "1792266009978")
        scheduled[0].action(None)

        coord.hass.async_create_task.assert_called_once_with(
            "refresh-coro", "verisure_italy_disarm_recheck"
        )
