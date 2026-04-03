"""Tests for AlarmState -> HA AlarmControlPanelState mapping.

Tests the pure mapping function in isolation — no homeassistant dependency.
Uses string values matching AlarmControlPanelState enum.
"""

from verisure_italy.models import (
    PROTO_TO_STATE,
    AlarmState,
    InteriorMode,
    PerimeterMode,
    ProtoCode,
)

# The mapping logic that the alarm_control_panel entity uses.
# Defined here as a pure function so tests run without homeassistant installed.

_DISARMED = AlarmState(interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF)
_PARTIAL_PERIMETER = AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON)
_TOTAL_PERIMETER = AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON)


def map_alarm_state(state: AlarmState) -> str:
    """Map two-axis AlarmState to HA AlarmControlPanelState string value."""
    if state == _DISARMED:
        return "disarmed"
    if state == _PARTIAL_PERIMETER:
        return "armed_home"
    if state == _TOTAL_PERIMETER:
        return "armed_away"
    return "armed_custom_bypass"


class TestStateMapping:
    def test_disarmed(self) -> None:
        state = PROTO_TO_STATE[ProtoCode.DISARMED]
        assert map_alarm_state(state) == "disarmed"

    def test_partial_perimeter_is_armed_home(self) -> None:
        state = PROTO_TO_STATE[ProtoCode.PARTIAL_PERIMETER]
        assert map_alarm_state(state) == "armed_home"

    def test_total_perimeter_is_armed_away(self) -> None:
        state = PROTO_TO_STATE[ProtoCode.TOTAL_PERIMETER]
        assert map_alarm_state(state) == "armed_away"

    def test_perimeter_only_is_custom_bypass(self) -> None:
        state = PROTO_TO_STATE[ProtoCode.PERIMETER_ONLY]
        assert map_alarm_state(state) == "armed_custom_bypass"

    def test_partial_no_perimeter_is_custom_bypass(self) -> None:
        state = PROTO_TO_STATE[ProtoCode.PARTIAL]
        assert map_alarm_state(state) == "armed_custom_bypass"

    def test_total_no_perimeter_is_custom_bypass(self) -> None:
        state = PROTO_TO_STATE[ProtoCode.TOTAL]
        assert map_alarm_state(state) == "armed_custom_bypass"

    def test_all_six_proto_codes_mapped(self) -> None:
        """Every proto code has a mapping — no gaps."""
        for code in ProtoCode:
            state = PROTO_TO_STATE[code]
            result = map_alarm_state(state)
            assert result in {
                "disarmed", "armed_home", "armed_away", "armed_custom_bypass"
            }, f"Proto {code} mapped to unexpected {result}"
