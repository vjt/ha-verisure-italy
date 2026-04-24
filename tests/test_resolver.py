"""Tests for CommandResolver — pure-function coverage.

Mirrors the decoded web-bundle resolver:
    docs/findings/arm-command-vocabulary.md
Every branch here maps 1:1 to a branch there.
"""

from __future__ import annotations

import pytest

from verisure_italy.exceptions import UnsupportedCommandError
from verisure_italy.models import (
    AlarmState,
    ArmCommand,
    InteriorMode,
    PerimeterMode,
    ServiceRequest,
)
from verisure_italy.resolver import CommandResolver

_OFF = AlarmState(interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF)
_PARTIAL = AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.OFF)
_PARTIAL_PERI = AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON)
_TOTAL = AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.OFF)
_TOTAL_PERI = AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON)
_PERI = AlarmState(interior=InteriorMode.OFF, perimeter=PerimeterMode.ON)

_SDVECU_SERVICES = frozenset({
    ServiceRequest.ARM, ServiceRequest.DARM, ServiceRequest.ARMDAY,
    ServiceRequest.ARMNIGHT, ServiceRequest.PERI,
})
_SDVFAST_SERVICES = frozenset({
    ServiceRequest.ARM, ServiceRequest.DARM, ServiceRequest.ARMDAY,
    ServiceRequest.ARMNIGHT, ServiceRequest.ARMINTFPART,
    ServiceRequest.ARMPARTFINT,
})


def _r(panel: str, services: frozenset[ServiceRequest]) -> CommandResolver:
    return CommandResolver(panel=panel, active_services=services)


# --- Disarm paths ---

def test_disarm_from_total_peri_uses_disarm_all() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES)
    assert r.resolve(target=_OFF, current=_TOTAL_PERI) == ArmCommand.DISARM_ALL


def test_disarm_from_total_only_uses_disarm() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES)
    assert r.resolve(target=_OFF, current=_TOTAL) == ArmCommand.DISARM


def test_disarm_sdvfast_uses_simple_disarm() -> None:
    r = _r("SDVFAST", _SDVFAST_SERVICES)
    assert r.resolve(target=_OFF, current=_TOTAL) == ArmCommand.DISARM


# --- Arm from off ---

def test_arm_total_from_off_peri_capable() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES)
    assert r.resolve(target=_TOTAL_PERI, current=_OFF) == ArmCommand.ARM_TOTAL_PERIMETER


def test_arm_partial_from_off_peri_capable() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES)
    assert r.resolve(target=_PARTIAL_PERI, current=_OFF) == ArmCommand.ARM_PARTIAL_PERIMETER


def test_arm_total_from_off_interior_only() -> None:
    r = _r("SDVFAST", _SDVFAST_SERVICES)
    assert r.resolve(target=_TOTAL, current=_OFF) == ArmCommand.ARM_TOTAL


def test_arm_partial_from_off_interior_only() -> None:
    r = _r("SDVFAST", _SDVFAST_SERVICES)
    assert r.resolve(target=_PARTIAL, current=_OFF) == ArmCommand.ARM_PARTIAL


# --- Armed-to-armed transitions (decoded bundle behaviour) ---

def test_transition_partial_to_total_uses_intfpart() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES)
    # Going from PARTIAL (currently armed interior) to TOTAL → ARMINTFPART1.
    out = r.resolve(target=_TOTAL, current=_PARTIAL)
    assert out == ArmCommand.ARM_TOTAL_FROM_PARTIAL_NIGHT


def test_transition_total_to_partial_uses_partfint_day() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES)
    out = r.resolve(target=_PARTIAL, current=_TOTAL)
    assert out == ArmCommand.ARM_PARTIAL_FROM_TOTAL


# --- Perimeter-only (SDVECU only) ---

def test_arm_perimeter_only() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES)
    assert r.resolve(target=_PERI, current=_OFF) == ArmCommand.ARM_PERIMETER


def test_arm_perimeter_rejected_on_interior_only_panel() -> None:
    r = _r("SDVFAST", _SDVFAST_SERVICES)
    with pytest.raises(UnsupportedCommandError) as exc:
        r.resolve(target=_PERI, current=_OFF)
    assert ServiceRequest.PERI in exc.value.missing_services


# --- Capability gating ---

def test_arm_total_peri_rejected_without_peri_service() -> None:
    services = frozenset({ServiceRequest.ARM, ServiceRequest.DARM})
    r = _r("SDVECU", services)
    with pytest.raises(UnsupportedCommandError):
        r.resolve(target=_TOTAL_PERI, current=_OFF)


# --- Degenerate ---

def test_noop_when_target_equals_current() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES)
    with pytest.raises(ValueError, match="current == target"):
        r.resolve(target=_OFF, current=_OFF)


def test_unknown_panel_rejected() -> None:
    r = _r("TOTALLY_FAKE", _SDVECU_SERVICES)
    with pytest.raises(ValueError, match="TOTALLY_FAKE"):
        r.resolve(target=_TOTAL, current=_OFF)
