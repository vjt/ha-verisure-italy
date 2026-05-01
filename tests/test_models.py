"""Tests for the Verisure alarm state model and API response parsing."""

import pytest
from pydantic import ValidationError

from verisure_italy.exceptions import UnexpectedStateError, UnsupportedCommandError
from verisure_italy.models import (
    PANEL_FAMILIES,
    PROTO_TO_STATE,
    STATE_TO_PROTO,
    SUPPORTED_PANELS,
    AlarmPartition,
    AlarmState,
    ArmCommand,
    GeneralStatus,
    InteriorMode,
    OperationResult,
    PanelFamily,
    PerimeterMode,
    ProtoCode,
    Service,
    ServiceRequest,
    ZoneException,
    active_services,
    effective_family,
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


def _svc(request: str, *, active: bool) -> Service:
    return Service.model_validate({
        "idService": 0,
        "active": active,
        "visible": True,
        "request": request,
    })


def test_active_services_returns_only_active_known_requests() -> None:
    services = [
        _svc("ARM", active=True),
        _svc("DARM", active=True),
        _svc("PERI", active=False),
        _svc("IMG", active=True),  # not in ServiceRequest — ignored
    ]
    assert active_services(services) == frozenset({
        ServiceRequest.ARM,
        ServiceRequest.DARM,
    })


def test_active_services_empty_input() -> None:
    assert active_services([]) == frozenset()


def test_unsupported_command_error_carries_context() -> None:
    err = UnsupportedCommandError(
        command=ArmCommand.ARM_TOTAL_PERIMETER,
        panel="SDVFAST",
        missing_services=frozenset({ServiceRequest.PERI}),
    )
    assert err.command is ArmCommand.ARM_TOTAL_PERIMETER
    assert err.panel == "SDVFAST"
    assert ServiceRequest.PERI in err.missing_services
    # Message must mention both the panel and the command for the log.
    assert "SDVFAST" in str(err)
    assert "ARM1PERI1" in str(err)


class TestEffectiveFamily:
    """`effective_family` is the single source of truth for the perimeter gate.

    Partition `02` (PERIMETRAL) `enter_states` non-empty means the user
    has perimeter-arm permission AND the install has perimeter sensors
    provisioned (the second is a precondition of the first). When empty,
    the panel rejects every `*PERI*` command — we demote at the family
    layer so the resolver never even tries.
    """

    def _peri_partitions(self, *, perimetral_enter: tuple[str, ...]) -> tuple[AlarmPartition, ...]:
        leave: tuple[str, ...] = ("01",) if perimetral_enter else ()
        return (
            AlarmPartition(id="01", enterStates=("01", "02"), leaveStates=("01", "02")),
            AlarmPartition(id="02", enterStates=perimetral_enter, leaveStates=leave),
            AlarmPartition(id="03", enterStates=(), leaveStates=()),
        )

    def test_peri_capable_with_perimeter_perm_stays_peri_capable(self) -> None:
        partitions = self._peri_partitions(perimetral_enter=("01",))

        assert effective_family("SDVECU", partitions) == PanelFamily.PERI_CAPABLE

    def test_peri_capable_without_perimeter_perm_demotes_to_interior_only(self) -> None:
        """laurafabry's SDVECU profile — partition 02 enterStates empty."""
        partitions = self._peri_partitions(perimetral_enter=())

        assert effective_family("SDVECU", partitions) == PanelFamily.INTERIOR_ONLY

    def test_interior_only_is_stable_with_or_without_partitions(self) -> None:
        # Partitions present (e.g. only MAIN populated, no PERIMETRAL row at all)
        partitions = (
            AlarmPartition(id="01", enterStates=("01",), leaveStates=("01",)),
        )

        assert effective_family("SDVFAST", partitions) == PanelFamily.INTERIOR_ONLY

    def test_unknown_panel_raises_keyerror(self) -> None:
        with pytest.raises(KeyError):
            effective_family("SOMETHING_NEW", ())

    def test_missing_perimetral_partition_treated_as_no_perimeter(self) -> None:
        """Fail-secure: if the API doesn't list partition `02` at all, no PERI."""
        partitions = (AlarmPartition(id="01", enterStates=("01",), leaveStates=("01",)),)

        assert effective_family("SDVECU", partitions) == PanelFamily.INTERIOR_ONLY

    def test_every_peri_capable_panel_demotes_uniformly(self) -> None:
        peri_capable_panels = [
            p for p, f in PANEL_FAMILIES.items() if f == PanelFamily.PERI_CAPABLE
        ]
        empty_partitions = self._peri_partitions(perimetral_enter=())

        for panel in peri_capable_panels:
            assert effective_family(panel, empty_partitions) == PanelFamily.INTERIOR_ONLY, (
                f"{panel} did not demote with empty perimeter perms"
            )


class TestAlarmPartition:
    """Pydantic parse for xSSrv.installation.configRepoUser.alarmPartitions[]."""

    def test_parses_main_partition(self) -> None:
        from verisure_italy.models import AlarmPartition

        partition = AlarmPartition.model_validate(
            {"id": "01", "enterStates": ["01", "02"], "leaveStates": ["01", "02"]}
        )

        assert partition.id == "01"
        assert partition.enter_states == ("01", "02")
        assert partition.leave_states == ("01", "02")

    def test_parses_empty_arrays(self) -> None:
        from verisure_italy.models import AlarmPartition

        partition = AlarmPartition.model_validate(
            {"id": "03", "enterStates": [], "leaveStates": []}
        )

        assert partition.id == "03"
        assert partition.enter_states == ()
        assert partition.leave_states == ()

    def test_is_frozen(self) -> None:
        from pydantic import ValidationError

        from verisure_italy.models import AlarmPartition

        partition = AlarmPartition.model_validate(
            {"id": "01", "enterStates": [], "leaveStates": []}
        )

        with pytest.raises(ValidationError):
            partition.id = "02"  # type: ignore[misc]
