"""Tests for the Verisure alarm state model and API response parsing."""

import pytest
from pydantic import ValidationError

from verisure_italy.exceptions import UnexpectedStateError
from verisure_italy.models import (
    PANEL_FAMILIES,
    PROTO_TO_STATE,
    STATE_TO_PROTO,
    SUPPORTED_PANELS,
    AlarmState,
    ArmCommand,
    GeneralStatus,
    InteriorMode,
    OperationResult,
    PanelFamily,
    PerimeterMode,
    ProtoCode,
    ServiceRequest,
    ZoneException,
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
        with pytest.raises(UnexpectedStateError, match="Unexpected alarm proto code"):
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
        with pytest.raises(UnexpectedStateError, match="Unexpected alarm proto code"):
            _ = result.proto_code

    def test_missing_required_field_raises(self) -> None:
        raw = {
            "res": "OK",
            "msg": "test",
            # missing numinst, protomResponse, protomResponseDate
        }
        with pytest.raises(ValidationError):
            OperationResult.model_validate(raw)


class TestZoneException:
    """Zone exception model parsing."""

    def test_parse_from_api_json(self) -> None:
        data = {"status": "OPEN", "deviceType": "MAGNETIC", "alias": "finestracucina"}
        exc = ZoneException.model_validate(data)
        assert exc.status == "OPEN"
        assert exc.device_type == "MAGNETIC"
        assert exc.alias == "finestracucina"

    def test_missing_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            ZoneException.model_validate({"status": "OPEN", "alias": "test"})


class TestGeneralStatusExceptions:
    """GeneralStatus model with exceptions field."""

    def test_parse_with_exceptions(self) -> None:
        raw = {
            "status": "B",
            "timestampUpdate": "1775164624581",
            "exceptions": [
                {"status": "OPEN", "deviceType": "MAGNETIC", "alias": "finestracucina"}
            ],
        }
        result = GeneralStatus.model_validate(raw)
        assert result.status == "B"
        assert result.exceptions is not None
        assert len(result.exceptions) == 1
        assert result.exceptions[0].alias == "finestracucina"

    def test_parse_without_exceptions(self) -> None:
        raw = {"status": "D", "timestampUpdate": "1775162828538"}
        result = GeneralStatus.model_validate(raw)
        assert result.exceptions is None

    def test_parse_with_null_exceptions(self) -> None:
        raw = {"status": "D", "timestampUpdate": "1775162828538", "exceptions": None}
        result = GeneralStatus.model_validate(raw)
        assert result.exceptions is None

    def test_parse_with_empty_exceptions(self) -> None:
        raw = {"status": "D", "timestampUpdate": "1775162828538", "exceptions": []}
        result = GeneralStatus.model_validate(raw)
        assert result.exceptions == []


class TestPanelFamilies:
    """Panel roster + family classifier from the Verisure web bundle."""

    def test_panel_families_covers_all_supported_panels(self) -> None:
        assert set(PANEL_FAMILIES.keys()) == SUPPORTED_PANELS

    def test_panel_family_a_is_peri_capable(self) -> None:
        peri_panels = {"SDVECU", "SDVECUD", "SDVECUW", "SDVECU-D", "SDVECU-W", "MODPRO"}
        for panel in peri_panels:
            assert PANEL_FAMILIES[panel] is PanelFamily.PERI_CAPABLE, panel

    def test_panel_family_b_has_no_perimeter(self) -> None:
        no_peri = {"SDVFAST", "SDVFSW"}
        for panel in no_peri:
            assert PANEL_FAMILIES[panel] is PanelFamily.INTERIOR_ONLY, panel

    def test_supported_panels_frozen(self) -> None:
        assert isinstance(SUPPORTED_PANELS, frozenset)
        assert len(SUPPORTED_PANELS) == 8


def test_arm_command_covers_full_wire_vocabulary() -> None:
    expected = {
        "DARM1",
        "DARM1DARMPERI",
        "DARMPERI",
        "DARMANNEX1",
        "ARM1",
        "ARM1PERI1",
        "ARMDAY1",
        "ARMDAY1PERI1",
        "ARMNIGHT1",
        "ARMNIGHT1PERI1",
        "ARMINTFPART1",
        "ARMPARTFINTDAY1",
        "ARMPARTFINTNIGHT1",
        "ARMANNEX1",
        "ARMINTEXT1",
        "PERI1",
    }
    assert {c.value for c in ArmCommand} == expected


def test_service_request_values() -> None:
    # Must match the strings in Service.request from xSSrv.
    assert ServiceRequest.ARM == "ARM"
    assert ServiceRequest.DARM == "DARM"
    assert ServiceRequest.ARMDAY == "ARMDAY"
    assert ServiceRequest.ARMNIGHT == "ARMNIGHT"
    assert ServiceRequest.PERI == "PERI"
    assert ServiceRequest.ARMANNEX == "ARMANNEX"
    assert ServiceRequest.DARMANNEX == "DARMANNEX"
    assert ServiceRequest.ARMINTFPART == "ARMINTFPART"
    assert ServiceRequest.ARMPARTFINT == "ARMPARTFINT"
