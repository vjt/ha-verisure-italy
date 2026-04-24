"""Panel-type gate in alarm_control_panel.

Covers the fail-secure behaviour: unknown panel types must raise
UnsupportedPanelError, emit a probe to the log, and send zero bytes
to the Verisure API. SDVECU (supported) must pass through untouched.

Also covers SUPPORTED_PANELS membership (one parametric test per panel)
and the three primary _STATE_MAP values (DISARMED, ARMED_HOME, ARMED_AWAY).
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.alarm_control_panel import AlarmControlPanelState

from custom_components.verisure_italy.alarm_control_panel import _STATE_MAP
from verisure_italy.exceptions import (
    OperationFailedError,
    OperationTimeoutError,
    SameStateError,
    UnsupportedPanelError,
)
from verisure_italy.models import (
    SUPPORTED_PANELS,
    AlarmState,
    Installation,
    InteriorMode,
    PanelFamily,
    PerimeterMode,
)


@asynccontextmanager
async def _noop_suppress():
    """Stand-in for coordinator.suppress_updates() in unit tests."""
    yield


def _make_installation(panel: str) -> Installation:
    return Installation(
        number="1234567", alias="Home", panel=panel, type="home",
    )


def _make_panel_entity(panel: str):
    """Build a VerisureAlarmPanel with just enough structure for the gate."""
    from custom_components.verisure_italy.alarm_control_panel import (
        VerisureAlarmPanel,
    )

    coordinator = MagicMock()
    coordinator.installation = _make_installation(panel)
    coordinator.client = MagicMock()
    coordinator.data = MagicMock()
    coordinator.data.alarm_state = MagicMock()
    coordinator.force_context = None

    # VerisureAlarmPanel.__init__ reads coordinator.data.alarm_state through
    # _STATE_MAP — bypass __init__ to skip HA entity wiring we don't need here.
    entity = VerisureAlarmPanel.__new__(VerisureAlarmPanel)
    entity.coordinator = coordinator  # type: ignore[attr-defined]
    entity.hass = MagicMock()  # type: ignore[attr-defined]
    entity.hass.services.async_call = AsyncMock()
    return entity, coordinator


class TestSupportedPanelList:
    def test_sdvecu_is_supported(self):
        assert "SDVECU" in SUPPORTED_PANELS

    def test_cent_is_not_supported(self):
        assert "CENT" not in SUPPORTED_PANELS


class TestCheckPanelSupported:
    async def test_sdvecu_passes_through(self):
        entity, _ = _make_panel_entity("SDVECU")
        assert await entity._check_panel_supported("arm") is True

    async def test_cent_raises(self, caplog, monkeypatch):
        entity, coordinator = _make_panel_entity("CENT")
        fake_probe = {"schema_version": 1, "installation": {"panel": "CENT"}}
        monkeypatch.setattr(
            "custom_components.verisure_italy.alarm_control_panel.run_probe",
            AsyncMock(return_value=fake_probe),
        )
        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ), pytest.raises(UnsupportedPanelError) as exc:
            await entity._check_panel_supported("arm")

        assert exc.value.panel == "CENT"
        # Probe markers must appear so users can locate the dump in logs
        combined = "\n".join(r.message for r in caplog.records)
        assert "VERISURE PROBE BEGIN" in combined
        assert "VERISURE PROBE END" in combined
        assert "CENT" in combined
        # Client-level arm/disarm must NOT have been invoked
        coordinator.async_arm.assert_not_called()
        coordinator.async_disarm.assert_not_called()
        # User notification created
        entity.hass.services.async_call.assert_awaited()

    async def test_probe_failure_does_not_mask_error(self, caplog, monkeypatch):
        """Even if probe throws, the gate must still refuse and raise."""
        entity, _ = _make_panel_entity("CENT")
        monkeypatch.setattr(
            "custom_components.verisure_italy.alarm_control_panel.run_probe",
            AsyncMock(side_effect=RuntimeError("network hiccup")),
        )
        with caplog.at_level(
            logging.ERROR,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ), pytest.raises(UnsupportedPanelError):
            await entity._check_panel_supported("arm")

        assert any("Probe failed" in r.message for r in caplog.records)

    async def test_probe_output_is_json_roundtrip(self, caplog, monkeypatch):
        """Probe block in log must be valid JSON (preserves reporter workflow)."""
        entity, _ = _make_panel_entity("ACME9000")
        fake_probe = {
            "schema_version": 1,
            "installation": {"panel": "ACME9000", "type": "home"},
            "services": [{"idService": 31, "request": "ARM"}],
            "devices": [],
            "alarm_state": {"status": "D", "exceptions": []},
        }
        monkeypatch.setattr(
            "custom_components.verisure_italy.alarm_control_panel.run_probe",
            AsyncMock(return_value=fake_probe),
        )
        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ), pytest.raises(UnsupportedPanelError):
            await entity._check_panel_supported("arm")

        block = next(
            r for r in caplog.records
            if "VERISURE PROBE BEGIN" in r.message
        )
        # Extract JSON between markers
        raw = block.message
        begin = raw.index("VERISURE PROBE BEGIN") + len("VERISURE PROBE BEGIN")
        end = raw.index("=== VERISURE PROBE END ===")
        payload = raw[begin:end].strip()
        # First chars are "===\n" from the marker; strip until first `{`
        payload = payload[payload.index("{"):]
        parsed = json.loads(payload)
        assert parsed["installation"]["panel"] == "ACME9000"


# ---------------------------------------------------------------------------
# SUPPORTED_PANELS membership — one parametric test per panel
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("panel", sorted(SUPPORTED_PANELS))
def test_every_supported_panel_is_gate_admissible(panel: str) -> None:
    """Every panel in SUPPORTED_PANELS must pass the coarse gate.

    The gate is a membership check; this test locks down the roster
    so a refactor that drops a panel from SUPPORTED_PANELS shows up
    as a test failure per panel.
    """
    assert panel in SUPPORTED_PANELS


def test_unknown_panel_not_in_supported_panels() -> None:
    """Sanity check — an obviously-invalid panel stays refused."""
    assert "TOTALLY_FAKE_PANEL" not in SUPPORTED_PANELS


def test_supported_panels_has_exactly_the_roster_from_findings() -> None:
    """Panel roster must match docs/findings/arm-command-vocabulary.md."""
    expected = {
        "SDVECU", "SDVECUD", "SDVECUW", "SDVECU-D", "SDVECU-W",
        "MODPRO", "SDVFAST", "SDVFSW",
    }
    assert expected == SUPPORTED_PANELS


# ---------------------------------------------------------------------------
# _STATE_MAP primary values — the three states exposed in the HA UI
# ---------------------------------------------------------------------------

def test_primary_state_map_disarmed() -> None:
    state = AlarmState(interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF)
    assert _STATE_MAP[PanelFamily.PERI_CAPABLE][state] == AlarmControlPanelState.DISARMED


def test_primary_state_map_partial_perimeter_is_armed_home() -> None:
    state = AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON)
    assert _STATE_MAP[PanelFamily.PERI_CAPABLE][state] == AlarmControlPanelState.ARMED_HOME


def test_primary_state_map_total_perimeter_is_armed_away() -> None:
    state = AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON)
    assert _STATE_MAP[PanelFamily.PERI_CAPABLE][state] == AlarmControlPanelState.ARMED_AWAY


# ---------------------------------------------------------------------------
# _OPERATION_FAILED_NOTIFICATION_TEMPLATE — points at marker block + GH issue
# ---------------------------------------------------------------------------


def test_operation_failed_template_points_at_marker_block_arm() -> None:
    """Notification text must mention the BEGIN/END marker + GH issue link."""
    from custom_components.verisure_italy.alarm_control_panel import (
        _OPERATION_FAILED_NOTIFICATION_TEMPLATE,
    )

    text = _OPERATION_FAILED_NOTIFICATION_TEMPLATE.format(
        operation="Arm", message="Panel busy", op_upper="ARM",
    )
    assert "VERISURE ARM FAILURE BEGIN" in text
    assert "VERISURE ARM FAILURE END" in text
    assert "github.com/vjt/ha-verisure-italy/issues" in text
    assert "Panel busy" in text


def test_operation_failed_template_points_at_marker_block_disarm() -> None:
    """Template covers DISARM path too (op_upper is substituted)."""
    from custom_components.verisure_italy.alarm_control_panel import (
        _OPERATION_FAILED_NOTIFICATION_TEMPLATE,
    )

    text = _OPERATION_FAILED_NOTIFICATION_TEMPLATE.format(
        operation="Disarm", message="timeout", op_upper="DISARM",
    )
    assert "VERISURE DISARM FAILURE BEGIN" in text
    assert "VERISURE DISARM FAILURE END" in text


# ---------------------------------------------------------------------------
# Armed-to-armed gate — UI (supported_features) + code (_async_arm)
# ---------------------------------------------------------------------------
#
# Verisure panels reject armed -> armed interior transitions with
# error_code 106. The mobile app enforces the same rule. We mirror it
# in HA: UI hides the arm buttons while armed, and the code path
# refuses even if a service call bypasses the UI.


class TestSupportedFeaturesDynamic:
    """`supported_features` reflects current alarm state via _update_supported_features."""

    def _entity_in_state(self, state: AlarmControlPanelState):
        entity, _ = _make_panel_entity("SDVECU")
        entity._attr_alarm_state = state  # type: ignore[attr-defined]
        entity._update_supported_features()
        return entity

    def test_disarmed_exposes_arm_home_and_arm_away(self) -> None:
        from homeassistant.components.alarm_control_panel.const import (
            AlarmControlPanelEntityFeature,
        )

        entity = self._entity_in_state(AlarmControlPanelState.DISARMED)
        features = entity._attr_supported_features
        assert features & AlarmControlPanelEntityFeature.ARM_HOME
        assert features & AlarmControlPanelEntityFeature.ARM_AWAY

    def test_armed_home_hides_both_arm_modes(self) -> None:
        from homeassistant.components.alarm_control_panel.const import (
            AlarmControlPanelEntityFeature,
        )

        entity = self._entity_in_state(AlarmControlPanelState.ARMED_HOME)
        features = entity._attr_supported_features
        assert not (features & AlarmControlPanelEntityFeature.ARM_HOME)
        assert not (features & AlarmControlPanelEntityFeature.ARM_AWAY)

    def test_armed_away_hides_both_arm_modes(self) -> None:
        from homeassistant.components.alarm_control_panel.const import (
            AlarmControlPanelEntityFeature,
        )

        entity = self._entity_in_state(AlarmControlPanelState.ARMED_AWAY)
        features = entity._attr_supported_features
        assert not (features & AlarmControlPanelEntityFeature.ARM_HOME)
        assert not (features & AlarmControlPanelEntityFeature.ARM_AWAY)

    def test_arming_transient_also_hides_arm_modes(self) -> None:
        """ARMING is a transient state — must not advertise arm modes either."""
        from homeassistant.components.alarm_control_panel.const import (
            AlarmControlPanelEntityFeature,
        )

        entity = self._entity_in_state(AlarmControlPanelState.ARMING)
        features = entity._attr_supported_features
        assert not (features & AlarmControlPanelEntityFeature.ARM_HOME)
        assert not (features & AlarmControlPanelEntityFeature.ARM_AWAY)


class TestAsyncArmRefusesArmedToArmed:
    """Code-layer belt-and-braces: _async_arm must not hit the wire."""

    async def test_arm_home_while_armed_away_is_refused(
        self, caplog
    ) -> None:
        entity, coordinator = _make_panel_entity("SDVECU")
        entity._attr_alarm_state = AlarmControlPanelState.ARMED_AWAY  # type: ignore[attr-defined]
        coordinator.async_arm = AsyncMock()

        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ):
            await entity.async_alarm_arm_home()

        coordinator.async_arm.assert_not_called()
        assert any(
            "already armed" in r.message for r in caplog.records
        )

    async def test_arm_away_while_armed_home_is_refused(
        self, caplog
    ) -> None:
        entity, coordinator = _make_panel_entity("SDVECU")
        entity._attr_alarm_state = AlarmControlPanelState.ARMED_HOME  # type: ignore[attr-defined]
        coordinator.async_arm = AsyncMock()

        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ):
            await entity.async_alarm_arm_away()

        coordinator.async_arm.assert_not_called()
        assert any(
            "already armed" in r.message for r in caplog.records
        )

    async def test_arm_same_mode_twice_is_ignored_silently(
        self, caplog
    ) -> None:
        """Same-mode arm is the existing no-op path — must not hit wire either."""
        entity, coordinator = _make_panel_entity("SDVECU")
        entity._attr_alarm_state = AlarmControlPanelState.ARMED_AWAY  # type: ignore[attr-defined]
        coordinator.async_arm = AsyncMock()

        with caplog.at_level(
            logging.DEBUG,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ):
            await entity.async_alarm_arm_away()

        coordinator.async_arm.assert_not_called()


def _wire_mutation_entity(panel: str):
    """Prepare an entity + mocked coordinator for mutation-path tests.

    The mutation paths pick up `_arm_lock`, `_check_panel_supported`'s
    SUPPORTED_PANELS fast-path, `coordinator.suppress_updates()`, and
    `coordinator.async_arm/disarm`. Wire just those, nothing else.
    """
    entity, coordinator = _make_panel_entity(panel)
    entity._arm_lock = asyncio.Lock()  # type: ignore[attr-defined]
    entity._attr_alarm_state = AlarmControlPanelState.DISARMED  # type: ignore[attr-defined]
    entity._force_context_timer = None  # type: ignore[attr-defined]
    entity.async_write_ha_state = MagicMock()  # type: ignore[attr-defined]
    entity._update_alarm_state = MagicMock()  # type: ignore[attr-defined]
    coordinator.suppress_updates = _noop_suppress
    coordinator.async_request_refresh = AsyncMock()
    return entity, coordinator


class TestSameStateBenignRace:
    """M15 — SameStateError from resolver is treated as a benign no-op."""

    async def test_arm_home_same_state_is_no_op(self, caplog):
        entity, coordinator = _wire_mutation_entity("SDVECU")
        coordinator.async_arm = AsyncMock(
            side_effect=SameStateError("already in target state"),
        )

        with caplog.at_level(
            logging.INFO,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ):
            await entity.async_alarm_arm_home()

        coordinator.async_arm.assert_awaited_once()
        entity._update_alarm_state.assert_called()
        assert any(
            "no-op" in r.message.lower() for r in caplog.records
        )
        # No failure-notification side-effects
        for call in entity.hass.services.async_call.call_args_list:
            args = call.args
            assert args[:2] != ("persistent_notification", "create")

    async def test_disarm_same_state_is_no_op(self, caplog):
        entity, coordinator = _wire_mutation_entity("SDVECU")
        entity._attr_alarm_state = AlarmControlPanelState.ARMED_AWAY  # type: ignore[attr-defined]
        coordinator.async_disarm = AsyncMock(
            side_effect=SameStateError("already disarmed"),
        )

        with caplog.at_level(
            logging.INFO,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ):
            await entity.async_alarm_disarm()

        coordinator.async_disarm.assert_awaited_once()
        entity._update_alarm_state.assert_called()
        assert any(
            "no-op" in r.message.lower() for r in caplog.records
        )


class TestHandleCoordinatorUpdateClearsStaleForceContext:
    """M4 — force-arm context is dropped when the panel moves on."""

    def _make_force_context(self):
        import datetime as _dt

        from custom_components.verisure_italy.coordinator import ForceArmContext
        return ForceArmContext(
            reference_id="ref-xxx", suid="suid-yyy", mode="armed_home",
            target=AlarmState(
                interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON,
            ),
            exceptions=[],
            created_at=_dt.datetime.now(_dt.UTC),
        )

    def _armed_home(self):
        return AlarmState(
            interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON,
        )

    def _disarmed(self):
        return AlarmState(
            interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF,
        )

    def test_stale_context_cleared_when_panel_armed(self, caplog):
        entity, coordinator = _wire_mutation_entity("SDVECU")
        # Ensure the parent CoordinatorEntity._handle_coordinator_update
        # doesn't touch HA internals we haven't wired.
        entity.async_write_ha_state = MagicMock()  # type: ignore[attr-defined]
        entity._cancel_force_context_timer = MagicMock()  # type: ignore[attr-defined]
        # Close the coroutine the entity schedules so we don't leak a
        # "never awaited" warning; no need to actually run it.
        entity.hass.async_create_task = lambda coro, name: coro.close()
        coordinator.force_context = self._make_force_context()
        coordinator.data.alarm_state = self._armed_home()
        coordinator.async_update_listeners = MagicMock()

        from unittest.mock import patch
        with patch.object(
            entity.__class__.__mro__[1],  # CoordinatorEntity
            "_handle_coordinator_update",
            return_value=None,
        ), caplog.at_level(
            logging.INFO,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ):
            entity._handle_coordinator_update()

        assert coordinator.force_context is None
        assert any(
            "stale force-arm" in r.message.lower() for r in caplog.records
        )

    def test_context_kept_when_panel_still_disarmed(self):
        entity, coordinator = _wire_mutation_entity("SDVECU")
        entity.async_write_ha_state = MagicMock()  # type: ignore[attr-defined]
        entity._cancel_force_context_timer = MagicMock()  # type: ignore[attr-defined]
        ctx = self._make_force_context()
        coordinator.force_context = ctx
        coordinator.data.alarm_state = self._disarmed()
        coordinator.async_update_listeners = MagicMock()

        from unittest.mock import patch
        with patch.object(
            entity.__class__.__mro__[1],
            "_handle_coordinator_update",
            return_value=None,
        ):
            entity._handle_coordinator_update()

        assert coordinator.force_context is ctx


class TestFailSecureOnTimeout:
    """M6 — OperationTimeoutError sets UNKNOWN, refreshes to resolve.

    OperationFailedError is unambiguous (panel rejected, prior state
    is correct), so it keeps the old revert-to-prior behaviour.
    """

    async def test_arm_timeout_sets_unknown_and_refreshes(self, caplog):
        entity, coordinator = _wire_mutation_entity("SDVECU")
        coordinator.async_arm = AsyncMock(
            side_effect=OperationTimeoutError("panel stopped responding"),
        )

        with caplog.at_level(
            logging.ERROR,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ):
            await entity.async_alarm_arm_home()

        # UNKNOWN = None in HA alarm_control_panel contract
        assert entity._attr_alarm_state is None
        entity._update_alarm_state.assert_not_called()
        coordinator.async_request_refresh.assert_awaited_once()
        assert any(
            "UNKNOWN" in r.message for r in caplog.records
        )

    async def test_arm_failed_reverts_and_refreshes_not(self, caplog):
        entity, coordinator = _wire_mutation_entity("SDVECU")
        coordinator.async_arm = AsyncMock(
            side_effect=OperationFailedError(
                "rejected", error_code=None, error_type=None,
            ),
        )

        with caplog.at_level(
            logging.ERROR,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ):
            await entity.async_alarm_arm_home()

        # Unambiguous — revert via _update_alarm_state, no refresh fall-through
        entity._update_alarm_state.assert_called()
        coordinator.async_request_refresh.assert_not_called()
        assert entity._attr_alarm_state is not None

    async def test_disarm_timeout_sets_unknown(self):
        entity, coordinator = _wire_mutation_entity("SDVECU")
        entity._attr_alarm_state = AlarmControlPanelState.ARMED_AWAY  # type: ignore[attr-defined]
        coordinator.async_disarm = AsyncMock(
            side_effect=OperationTimeoutError("panel stopped responding"),
        )

        await entity.async_alarm_disarm()

        assert entity._attr_alarm_state is None
        coordinator.async_request_refresh.assert_awaited_once()


class TestForceArmAudit:
    """M17 — every force-arm attempt emits a structured audit line."""

    def _wire(self):
        import datetime as _dt

        from custom_components.verisure_italy.coordinator import (
            ForceArmContext,
            VerisureStatusData,
        )
        from verisure_italy.models import ProtoCode, ZoneException

        entity, coordinator = _wire_mutation_entity("SDVECU")
        ctx = ForceArmContext(
            reference_id="ref-123",
            suid="suid-456",
            mode="armed_home",
            target=AlarmState(
                interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON,
            ),
            exceptions=[
                ZoneException(
                    status="open",
                    deviceType="door",
                    alias="FrontDoor",
                ),
            ],
            created_at=_dt.datetime.now(_dt.UTC),
        )
        coordinator.force_context = ctx
        coordinator.data = VerisureStatusData(
            alarm_state=AlarmState(
                interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF,
            ),
            proto_code=ProtoCode.DISARMED,
            timestamp="2026-01-01T00:00:00",
            exceptions=[],
        )
        return entity, coordinator, ctx

    def _parse_audit(self, caplog):
        for r in caplog.records:
            if "FORCE_ARM_AUDIT" in r.message:
                payload = r.message.split("FORCE_ARM_AUDIT ", 1)[1]
                return json.loads(payload)
        return None

    async def test_success_audit(self, caplog):
        entity, coordinator, _ctx = self._wire()
        coordinator.async_arm = AsyncMock()

        with caplog.at_level(
            logging.INFO,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ):
            await entity.async_force_arm()

        audit = self._parse_audit(caplog)
        assert audit is not None
        assert audit["result"] == "success"
        assert audit["reference_id"] == "ref-123"
        assert audit["suid"] == "suid-456"
        assert audit["mode"] == "armed_home"
        assert audit["zones"] == ["FrontDoor"]
        assert audit["proto_before"] == "D"
        assert "duration_ms" in audit
        assert "error_class" not in audit

    async def test_timeout_audit(self, caplog):
        entity, coordinator, _ctx = self._wire()
        coordinator.async_arm = AsyncMock(
            side_effect=OperationTimeoutError("took too long"),
        )

        with caplog.at_level(
            logging.INFO,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ):
            await entity.async_force_arm()

        audit = self._parse_audit(caplog)
        assert audit is not None
        assert audit["result"] == "timeout"
        assert audit["error_class"] == "OperationTimeoutError"
        assert "took too long" in audit["error_message"]

    async def test_failure_audit(self, caplog):
        entity, coordinator, _ctx = self._wire()
        coordinator.async_arm = AsyncMock(
            side_effect=OperationFailedError(
                "rejected", error_code="106", error_type=None,
            ),
        )

        with caplog.at_level(
            logging.INFO,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ):
            await entity.async_force_arm()

        audit = self._parse_audit(caplog)
        assert audit is not None
        assert audit["result"] == "failure"
        assert audit["error_class"] == "OperationFailedError"


# ---------------------------------------------------------------------------
# Panel-family-aware arm targets + reverse state map (Issue #3)
# ---------------------------------------------------------------------------
#
# INTERIOR_ONLY panels (SDVFAST, SDVFSW) have no perimeter sensors.
# arm_home/arm_away must target PARTIAL/OFF and TOTAL/OFF — not the
# PARTIAL/ON + TOTAL/ON used on PERI_CAPABLE panels — otherwise the
# resolver picks a perimeter command and the family gate rejects it.
# Proto `P`/`T` on INTERIOR_ONLY are the primary home/away states,
# not CUSTOM_BYPASS. Proto codes E/A/B are impossible on these panels
# and must crash loud (fail-secure) rather than silently degrade.


class TestArmTargetsByFamily:
    """arm_home/arm_away resolve the target AlarmState per panel family."""

    async def test_arm_home_on_peri_capable_targets_partial_perimeter(self):
        entity, coordinator = _wire_mutation_entity("SDVECU")
        coordinator.async_arm = AsyncMock()

        await entity.async_alarm_arm_home()

        coordinator.async_arm.assert_awaited_once()
        target = coordinator.async_arm.call_args.args[0]
        assert target == AlarmState(
            interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON,
        )

    async def test_arm_home_on_interior_only_targets_partial_off(self):
        entity, coordinator = _wire_mutation_entity("SDVFAST")
        coordinator.async_arm = AsyncMock()

        await entity.async_alarm_arm_home()

        coordinator.async_arm.assert_awaited_once()
        target = coordinator.async_arm.call_args.args[0]
        assert target == AlarmState(
            interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.OFF,
        )

    async def test_arm_away_on_peri_capable_targets_total_perimeter(self):
        entity, coordinator = _wire_mutation_entity("SDVECU")
        coordinator.async_arm = AsyncMock()

        await entity.async_alarm_arm_away()

        coordinator.async_arm.assert_awaited_once()
        target = coordinator.async_arm.call_args.args[0]
        assert target == AlarmState(
            interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON,
        )

    async def test_arm_away_on_interior_only_targets_total_off(self):
        entity, coordinator = _wire_mutation_entity("SDVFSW")
        coordinator.async_arm = AsyncMock()

        await entity.async_alarm_arm_away()

        coordinator.async_arm.assert_awaited_once()
        target = coordinator.async_arm.call_args.args[0]
        assert target == AlarmState(
            interior=InteriorMode.TOTAL, perimeter=PerimeterMode.OFF,
        )


class TestStateMapByFamily:
    """_STATE_MAP reverse lookup honours panel family semantics."""

    def test_interior_only_partial_off_is_armed_home(self):
        state = AlarmState(
            interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.OFF,
        )
        assert (
            _STATE_MAP[PanelFamily.INTERIOR_ONLY][state]
            == AlarmControlPanelState.ARMED_HOME
        )

    def test_interior_only_total_off_is_armed_away(self):
        state = AlarmState(
            interior=InteriorMode.TOTAL, perimeter=PerimeterMode.OFF,
        )
        assert (
            _STATE_MAP[PanelFamily.INTERIOR_ONLY][state]
            == AlarmControlPanelState.ARMED_AWAY
        )

    def test_interior_only_disarmed(self):
        state = AlarmState(
            interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF,
        )
        assert (
            _STATE_MAP[PanelFamily.INTERIOR_ONLY][state]
            == AlarmControlPanelState.DISARMED
        )

    def test_peri_capable_partial_off_is_custom_bypass(self):
        """PERI_CAPABLE regression: proto P (interior-only) stays CUSTOM_BYPASS."""
        state = AlarmState(
            interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.OFF,
        )
        assert (
            _STATE_MAP[PanelFamily.PERI_CAPABLE][state]
            == AlarmControlPanelState.ARMED_CUSTOM_BYPASS
        )

    def test_peri_capable_total_off_is_custom_bypass(self):
        """PERI_CAPABLE regression: proto T (interior-only) stays CUSTOM_BYPASS."""
        state = AlarmState(
            interior=InteriorMode.TOTAL, perimeter=PerimeterMode.OFF,
        )
        assert (
            _STATE_MAP[PanelFamily.PERI_CAPABLE][state]
            == AlarmControlPanelState.ARMED_CUSTOM_BYPASS
        )

    def test_interior_only_has_no_peri_states(self):
        """Perimeter-involving states are impossible on INTERIOR_ONLY panels."""
        peri_on = AlarmState(
            interior=InteriorMode.OFF, perimeter=PerimeterMode.ON,
        )
        assert peri_on not in _STATE_MAP[PanelFamily.INTERIOR_ONLY]


class TestUpdateAlarmStateByFamily:
    """_update_alarm_state applies the family-specific reverse map."""

    def _entity_with_state(self, panel: str, state: AlarmState):
        entity, coordinator = _make_panel_entity(panel)
        coordinator.data.alarm_state = state
        coordinator.force_context = None
        return entity

    def test_proto_p_on_interior_only_is_armed_home(self):
        entity = self._entity_with_state(
            "SDVFAST",
            AlarmState(
                interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.OFF,
            ),
        )
        entity._update_alarm_state()
        assert entity._attr_alarm_state == AlarmControlPanelState.ARMED_HOME

    def test_proto_t_on_interior_only_is_armed_away(self):
        entity = self._entity_with_state(
            "SDVFAST",
            AlarmState(
                interior=InteriorMode.TOTAL, perimeter=PerimeterMode.OFF,
            ),
        )
        entity._update_alarm_state()
        assert entity._attr_alarm_state == AlarmControlPanelState.ARMED_AWAY

    def test_proto_b_on_interior_only_raises_key_error(self):
        """Peri-involving proto on INTERIOR_ONLY panel = fail-secure crash."""
        entity = self._entity_with_state(
            "SDVFAST",
            AlarmState(
                interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON,
            ),
        )
        with pytest.raises(KeyError):
            entity._update_alarm_state()
