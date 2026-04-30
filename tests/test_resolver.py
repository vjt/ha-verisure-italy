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

# Live-verified SDVECU service set (from a real panel's xSSrv response).
# Note: ARMDAY and PERI are NOT listed as separate active services on
# SDVECU, yet the panel accepts ARMDAY1, ARM1PERI1, ARMDAY1PERI1, and
# DARM1DARMPERI fine. EST IS listed (perimeter sensors provisioned)
# and is the runtime indicator effective_family() consults to keep this
# install in PERI_CAPABLE rather than demoting to INTERIOR_ONLY.
# See docs/findings/panel-SDVECU-probe.json.
_SDVECU_SERVICES = frozenset({
    ServiceRequest.ARM, ServiceRequest.DARM, ServiceRequest.ARMNIGHT,
    ServiceRequest.EST,
})
# SDVFAST (issue #3 reporter probe): explicit ARMDAY / ARMNIGHT /
# ARMINTFPART / ARMPARTFINT entries, no PERI. Interior-only family.
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


# --- Armed-to-armed transitions ---
#
# Single-step transition commands (ARMPARTFINTDAY1 etc.) are only emitted
# when the panel exposes the relevant service. SDVECU doesn't list
# ARMINTFPART / ARMPARTFINT in xSSrv and rejects those commands on the
# wire with "Request not valid for Central Unit"; for SDVECU we fall
# through to the target-only arm command (which IS accepted).

def test_sdvfast_transition_partial_to_total_uses_intfpart() -> None:
    """SDVFAST has ARMINTFPART active → single-step transition."""
    r = _r("SDVFAST", _SDVFAST_SERVICES)
    out = r.resolve(target=_TOTAL, current=_PARTIAL)
    assert out == ArmCommand.ARM_TOTAL_FROM_ARMED_INTERIOR


def test_sdvfast_transition_total_to_partial_uses_partfint_day() -> None:
    """SDVFAST has ARMPARTFINT active → single-step transition."""
    r = _r("SDVFAST", _SDVFAST_SERVICES)
    out = r.resolve(target=_PARTIAL, current=_TOTAL)
    assert out == ArmCommand.ARM_PARTIAL_FROM_TOTAL


def test_sdvecu_partial_to_total_falls_through_to_arm_total() -> None:
    """SDVECU without ARMINTFPART service → target-only ARM_TOTAL."""
    r = _r("SDVECU", _SDVECU_SERVICES)
    out = r.resolve(target=_TOTAL, current=_PARTIAL)
    assert out == ArmCommand.ARM_TOTAL


def test_sdvecu_total_peri_to_partial_peri_uses_arm_partial_peri() -> None:
    """Live-observed regression: SDVECU rejects ARMPARTFINTDAY1.

    Resolver must fall through to ARMDAY1PERI1 (ARM_PARTIAL_PERIMETER),
    which SDVECU accepts — matches pre-v0.9.0 behaviour.
    """
    r = _r("SDVECU", _SDVECU_SERVICES)
    out = r.resolve(target=_PARTIAL_PERI, current=_TOTAL_PERI)
    assert out == ArmCommand.ARM_PARTIAL_PERIMETER


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

def test_arm_night_rejected_without_armnight_service() -> None:
    """ARMNIGHT is a reliably-reported sub-capability; its absence gates."""
    services = frozenset({ServiceRequest.ARM, ServiceRequest.DARM})
    r = _r("SDVECU", services)
    target_night = AlarmState(
        interior=InteriorMode.TOTAL, perimeter=PerimeterMode.OFF,
    )
    # ARM_NIGHT is unreachable via AlarmState today (no NIGHT variant in
    # InteriorMode), so this test uses the capability-gate path directly
    # to ensure the mapping still rejects when ARMNIGHT is missing.
    from verisure_italy.resolver import _COMMAND_REQUIRES
    assert ServiceRequest.ARMNIGHT in _COMMAND_REQUIRES[ArmCommand.ARM_NIGHT]
    # Sanity: ARM_TOTAL (no NIGHT dep) does pass with the minimal set.
    assert r.resolve(target=target_night, current=_OFF) == ArmCommand.ARM_TOTAL


def test_base_arm_service_required_for_every_arm_variant() -> None:
    """No ARM service = every arm command refused.

    Seed EST so the perimeter family-gate (which would otherwise fire
    first on a no-EST PERI_CAPABLE install) is satisfied; we want the
    test to land on the missing-ARM check specifically.
    """
    services = frozenset({ServiceRequest.DARM, ServiceRequest.EST})
    r = _r("SDVECU", services)
    with pytest.raises(UnsupportedCommandError) as exc:
        r.resolve(target=_TOTAL_PERI, current=_OFF)
    assert ServiceRequest.ARM in exc.value.missing_services


# --- Degenerate ---

def test_noop_when_target_equals_current() -> None:
    from verisure_italy.exceptions import SameStateError
    r = _r("SDVECU", _SDVECU_SERVICES)
    with pytest.raises(SameStateError, match="already in target state"):
        r.resolve(target=_OFF, current=_OFF)


def test_unknown_panel_rejected() -> None:
    r = _r("TOTALLY_FAKE", _SDVECU_SERVICES)
    with pytest.raises(ValueError, match="TOTALLY_FAKE"):
        r.resolve(target=_TOTAL, current=_OFF)


# --- Cross-perimeter armed transitions (security guard) ---

def test_cross_peri_armed_to_armed_rejected() -> None:
    """Cross-perimeter transition from any armed state must raise.

    current=TOTAL_PERI → target=PARTIAL (perimeter OFF) previously
    silently returned ARMDAY1 — applied to an armed panel, that's
    the wrong wire command. Fail-secure: raise so the caller must
    disarm first.
    """
    r = _r("SDVECU", _SDVECU_SERVICES)
    with pytest.raises(ValueError, match="Cross-perimeter armed transition"):
        r.resolve(target=_PARTIAL, current=_TOTAL_PERI)


def test_cross_peri_from_partial_peri_to_total_rejected() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES)
    with pytest.raises(ValueError, match="Cross-perimeter armed transition"):
        r.resolve(target=_TOTAL, current=_PARTIAL_PERI)


def test_cross_peri_from_peri_only_to_total_does_not_trigger() -> None:
    """current=OFF/ON is NOT an armed interior; this branch must NOT fire.

    Going from perimeter-only (interior OFF) to total arm is a
    normal arm-from-disarmed path; returns ARM_TOTAL.
    """
    r = _r("SDVECU", _SDVECU_SERVICES)
    out = r.resolve(target=_TOTAL, current=_PERI)
    assert out == ArmCommand.ARM_TOTAL


# --- v0.9.3: PERI_CAPABLE-no-EST demotion (Issue #4) ---
#
# An SDVECU model can ship without perimeter sensors provisioned.
# Issue #4 reporter laurafabry's xSSrv was [ARM, ARMNIGHT, DARM] (no EST);
# the panel rejected ARMDAY1PERI1 / ARM1PERI1 with code 101
# error_mpj_exception. effective_family() now demotes such installs to
# INTERIOR_ONLY so the resolver refuses *PERI* commands client-side
# rather than letting the panel reject them on the wire.

# Issue #4 reporter's exact service set — SDVECU, no EST.
_SDVECU_NO_EST_SERVICES = frozenset({
    ServiceRequest.ARM, ServiceRequest.DARM, ServiceRequest.ARMNIGHT,
})


def test_sdvecu_without_est_rejects_arm_partial_perimeter() -> None:
    """Issue #4: SDVECU + no EST → ARM_PARTIAL_PERIMETER refused with EST missing."""
    r = _r("SDVECU", _SDVECU_NO_EST_SERVICES)
    with pytest.raises(UnsupportedCommandError) as exc:
        r.resolve(target=_PARTIAL_PERI, current=_OFF)
    assert ServiceRequest.EST in exc.value.missing_services
    assert exc.value.command == ArmCommand.ARM_PARTIAL_PERIMETER


def test_sdvecu_without_est_rejects_arm_total_perimeter() -> None:
    """Issue #4: SDVECU + no EST → ARM_TOTAL_PERIMETER refused with EST missing."""
    r = _r("SDVECU", _SDVECU_NO_EST_SERVICES)
    with pytest.raises(UnsupportedCommandError) as exc:
        r.resolve(target=_TOTAL_PERI, current=_OFF)
    assert ServiceRequest.EST in exc.value.missing_services
    assert exc.value.command == ArmCommand.ARM_TOTAL_PERIMETER


def test_sdvecu_without_est_accepts_interior_only_arm() -> None:
    """Demoted SDVECU still arms via the INTERIOR_ONLY path (ARMDAY1, ARM1)."""
    r = _r("SDVECU", _SDVECU_NO_EST_SERVICES)
    assert (
        r.resolve(target=_PARTIAL, current=_OFF) == ArmCommand.ARM_PARTIAL
    )
    assert (
        r.resolve(target=_TOTAL, current=_OFF) == ArmCommand.ARM_TOTAL
    )


def test_sdvfast_missing_perimeter_reports_peri_not_est() -> None:
    """Model-level INTERIOR_ONLY (no perimeter hardware) reports PERI missing.

    Distinguishes: PERI_CAPABLE-no-EST = missing EST (sensors not
    provisioned); INTERIOR_ONLY = missing PERI (model has no perimeter
    hardware at all). Issue body should point at the right service.
    """
    r = _r("SDVFAST", _SDVFAST_SERVICES)
    with pytest.raises(UnsupportedCommandError) as exc:
        r.resolve(target=_TOTAL_PERI, current=_OFF)
    assert ServiceRequest.PERI in exc.value.missing_services
    assert ServiceRequest.EST not in exc.value.missing_services
