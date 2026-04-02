"""Tests for the Verisure alarm state model and API response parsing."""

import pytest
from pydantic import ValidationError

from verisure_api.models import (
    PROTO_TO_STATE,
    STATE_TO_COMMAND,
    STATE_TO_PROTO,
    AlarmState,
    ArmCommand,
    InteriorMode,
    OperationResult,
    PerimeterMode,
    ProtoCode,
    parse_proto_code,
)


class TestProtoCodeParsing:
    """Proto code parsing: known codes succeed, unknown codes crash."""

    @pytest.mark.parametrize(
        "code",
        ["D", "E", "P", "B", "T", "A"],
    )
    def test_valid_proto_codes(self, code: str) -> None:
        result = parse_proto_code(code)
        assert isinstance(result, ProtoCode)
        assert result.value == code

    @pytest.mark.parametrize(
        "code",
        ["Q", "C", "X", "", "d", "disarmed", "ARMED"],
    )
    def test_invalid_proto_codes_raise(self, code: str) -> None:
        with pytest.raises(ValueError, match="Unknown proto response code"):
            parse_proto_code(code)


class TestAlarmStateModel:
    """The two-axis state model: six valid states, bijective mapping."""

    def test_exactly_six_states(self) -> None:
        assert len(PROTO_TO_STATE) == 6

    def test_proto_to_state_is_bijective(self) -> None:
        """Every proto code maps to a unique state and back."""
        states = list(PROTO_TO_STATE.values())
        assert len(set(states)) == len(states), "Duplicate states in PROTO_TO_STATE"

        for code, state in PROTO_TO_STATE.items():
            assert STATE_TO_PROTO[state] == code

    def test_every_state_has_a_command(self) -> None:
        """Every reachable state has a command to get there."""
        for state in PROTO_TO_STATE.values():
            assert state in STATE_TO_COMMAND, f"No command for state {state}"

    def test_disarmed_state(self) -> None:
        state = PROTO_TO_STATE[ProtoCode.DISARMED]
        assert state.interior == InteriorMode.OFF
        assert state.perimeter == PerimeterMode.OFF

    def test_total_perimeter_state(self) -> None:
        state = PROTO_TO_STATE[ProtoCode.TOTAL_PERIMETER]
        assert state.interior == InteriorMode.TOTAL
        assert state.perimeter == PerimeterMode.ON

    def test_partial_perimeter_state(self) -> None:
        state = PROTO_TO_STATE[ProtoCode.PARTIAL_PERIMETER]
        assert state.interior == InteriorMode.PARTIAL
        assert state.perimeter == PerimeterMode.ON

    def test_alarm_state_is_frozen(self) -> None:
        state = AlarmState(interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF)
        with pytest.raises(ValidationError):
            state.interior = InteriorMode.TOTAL  # type: ignore[misc]


class TestOperationResultParsing:
    """API response parsing: valid responses parse, invalid ones crash."""

    def test_parse_disarmed_response(self) -> None:
        raw = {
            "res": "OK",
            "msg": "alarm-manager.inactive_alarm",
            "status": None,
            "numinst": "1234567",
            "protomResponse": "D",
            "protomResponseDate": "2026-04-02T18:37:11Z",
        }
        result = OperationResult.model_validate(raw)
        assert result.proto_code == ProtoCode.DISARMED
        assert result.alarm_state.interior == InteriorMode.OFF
        assert result.alarm_state.perimeter == PerimeterMode.OFF
        assert not result.is_pending

    def test_parse_total_perimeter_response(self) -> None:
        raw = {
            "res": "OK",
            "msg": "alarm-manager.active_perimeter_plus_alarm",
            "status": None,
            "numinst": "1234567",
            "protomResponse": "A",
            "protomResponseDate": "2026-04-02T18:00:05Z",
        }
        result = OperationResult.model_validate(raw)
        assert result.proto_code == ProtoCode.TOTAL_PERIMETER
        assert result.alarm_state.interior == InteriorMode.TOTAL
        assert result.alarm_state.perimeter == PerimeterMode.ON

    def test_parse_pending_response(self) -> None:
        raw = {
            "res": "WAIT",
            "msg": "",
            "status": None,
            "numinst": "1234567",
            "protomResponse": "",
            "protomResponseDate": "",
        }
        result = OperationResult.model_validate(raw)
        assert result.is_pending

    def test_unknown_proto_code_raises_on_access(self) -> None:
        raw = {
            "res": "OK",
            "msg": "something weird",
            "status": None,
            "numinst": "1234567",
            "protomResponse": "Z",
            "protomResponseDate": "2026-04-02T18:00:05Z",
        }
        result = OperationResult.model_validate(raw)
        with pytest.raises(ValueError, match="Unknown proto response code"):
            _ = result.proto_code

    def test_missing_required_field_raises(self) -> None:
        raw = {
            "res": "OK",
            "msg": "test",
            # missing numinst, protomResponse, protomResponseDate
        }
        with pytest.raises(ValidationError):
            OperationResult.model_validate(raw)


class TestArmCommands:
    """Arm commands map correctly to target states."""

    def test_disarm_command(self) -> None:
        disarmed = PROTO_TO_STATE[ProtoCode.DISARMED]
        assert STATE_TO_COMMAND[disarmed] == ArmCommand.DISARM_ALL

    def test_total_perimeter_command(self) -> None:
        state = PROTO_TO_STATE[ProtoCode.TOTAL_PERIMETER]
        assert STATE_TO_COMMAND[state] == ArmCommand.ARM_TOTAL_PERIMETER

    def test_partial_perimeter_command(self) -> None:
        state = PROTO_TO_STATE[ProtoCode.PARTIAL_PERIMETER]
        assert STATE_TO_COMMAND[state] == ArmCommand.ARM_PARTIAL_PERIMETER
