"""Tests for CommandResolver — pure-function coverage.

Mirrors the decoded web-bundle resolver:
    docs/findings/arm-command-vocabulary.md
Every branch here maps 1:1 to a branch there.
"""

from __future__ import annotations

import pytest

from verisure_italy.exceptions import UnsupportedCommandError
from verisure_italy.models import (
    AlarmPartition,
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
# DARM1DARMPERI fine. See docs/findings/panel-SDVECU-probe.json.
_SDVECU_SERVICES = frozenset(
    {
        ServiceRequest.ARM,
        ServiceRequest.DARM,
        ServiceRequest.ARMNIGHT,
    }
)
# SDVFAST (issue #3 reporter probe): explicit ARMDAY / ARMNIGHT /
# ARMINTFPART / ARMPARTFINT entries, no PERI. Interior-only family.
_SDVFAST_SERVICES = frozenset(
    {
        ServiceRequest.ARM,
        ServiceRequest.DARM,
        ServiceRequest.ARMDAY,
        ServiceRequest.ARMNIGHT,
        ServiceRequest.ARMINTFPART,
        ServiceRequest.ARMPARTFINT,
    }
)

# Partition tuples — used to drive effective_family() in the resolver.
# A positive partition 02 (non-empty enterStates) = perimeter provisioned.
_PARTITIONS_WITH_PERIMETER: tuple[AlarmPartition, ...] = (
    AlarmPartition(id="01", enterStates=("01", "02"), leaveStates=("01", "02")),
    AlarmPartition(id="02", enterStates=("01",), leaveStates=("01",)),
    AlarmPartition(id="03", enterStates=(), leaveStates=()),
)
_PARTITIONS_WITHOUT_PERIMETER: tuple[AlarmPartition, ...] = (
    AlarmPartition(id="01", enterStates=("01", "02"), leaveStates=("01", "02")),
    AlarmPartition(id="02", enterStates=(), leaveStates=()),
    AlarmPartition(id="03", enterStates=(), leaveStates=()),
)


def _r(
    panel: str,
    services: frozenset[ServiceRequest],
    alarm_partitions: tuple[AlarmPartition, ...],
) -> CommandResolver:
    """Construct a CommandResolver. `alarm_partitions` is required:

    - `_PARTITIONS_WITH_PERIMETER` for tests exercising the perimeter path
      (laurafabry profile inverse — provisioned + permitted).
    - `_PARTITIONS_WITHOUT_PERIMETER` for tests exercising the
      partition-gate demotion path (laurafabry profile).
    - `()` for tests of model-level INTERIOR_ONLY panels (SDVFAST etc.) —
      partition data is irrelevant there but the explicit `()` makes the
      intent visible to the reader.
    """
    return CommandResolver(
        panel=panel,
        active_services=services,
        alarm_partitions=alarm_partitions,
    )


# --- Disarm paths ---


def test_disarm_from_total_peri_uses_disarm_all() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES, _PARTITIONS_WITH_PERIMETER)
    assert r.resolve(target=_OFF, current=_TOTAL_PERI) == ArmCommand.DISARM_ALL


def test_disarm_from_total_only_uses_disarm() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES, _PARTITIONS_WITH_PERIMETER)
    assert r.resolve(target=_OFF, current=_TOTAL) == ArmCommand.DISARM


def test_disarm_sdvfast_uses_simple_disarm() -> None:
    r = _r("SDVFAST", _SDVFAST_SERVICES, ())
    assert r.resolve(target=_OFF, current=_TOTAL) == ArmCommand.DISARM


# --- Arm from off ---


def test_arm_total_from_off_peri_capable() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES, _PARTITIONS_WITH_PERIMETER)
    assert r.resolve(target=_TOTAL_PERI, current=_OFF) == ArmCommand.ARM_TOTAL_PERIMETER


def test_arm_partial_from_off_peri_capable() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES, _PARTITIONS_WITH_PERIMETER)
    assert r.resolve(target=_PARTIAL_PERI, current=_OFF) == ArmCommand.ARM_PARTIAL_PERIMETER


def test_arm_total_from_off_interior_only() -> None:
    r = _r("SDVFAST", _SDVFAST_SERVICES, ())
    assert r.resolve(target=_TOTAL, current=_OFF) == ArmCommand.ARM_TOTAL


def test_arm_partial_from_off_interior_only() -> None:
    r = _r("SDVFAST", _SDVFAST_SERVICES, ())
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
    r = _r("SDVFAST", _SDVFAST_SERVICES, ())
    out = r.resolve(target=_TOTAL, current=_PARTIAL)
    assert out == ArmCommand.ARM_TOTAL_FROM_ARMED_INTERIOR


def test_sdvfast_transition_total_to_partial_uses_partfint_day() -> None:
    """SDVFAST has ARMPARTFINT active → single-step transition."""
    r = _r("SDVFAST", _SDVFAST_SERVICES, ())
    out = r.resolve(target=_PARTIAL, current=_TOTAL)
    assert out == ArmCommand.ARM_PARTIAL_FROM_TOTAL


def test_sdvecu_partial_to_total_falls_through_to_arm_total() -> None:
    """SDVECU without ARMINTFPART service → target-only ARM_TOTAL."""
    r = _r("SDVECU", _SDVECU_SERVICES, _PARTITIONS_WITH_PERIMETER)
    out = r.resolve(target=_TOTAL, current=_PARTIAL)
    assert out == ArmCommand.ARM_TOTAL


def test_sdvecu_total_peri_to_partial_peri_uses_arm_partial_peri() -> None:
    """Live-observed regression: SDVECU rejects ARMPARTFINTDAY1.

    Resolver must fall through to ARMDAY1PERI1 (ARM_PARTIAL_PERIMETER),
    which SDVECU accepts — matches pre-v0.9.0 behaviour.
    """
    r = _r("SDVECU", _SDVECU_SERVICES, _PARTITIONS_WITH_PERIMETER)
    out = r.resolve(target=_PARTIAL_PERI, current=_TOTAL_PERI)
    assert out == ArmCommand.ARM_PARTIAL_PERIMETER


# --- Perimeter-only (SDVECU only) ---


def test_arm_perimeter_only() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES, _PARTITIONS_WITH_PERIMETER)
    assert r.resolve(target=_PERI, current=_OFF) == ArmCommand.ARM_PERIMETER


def test_arm_perimeter_rejected_on_interior_only_panel() -> None:
    r = _r("SDVFAST", _SDVFAST_SERVICES, ())
    with pytest.raises(UnsupportedCommandError) as exc:
        r.resolve(target=_PERI, current=_OFF)
    assert ServiceRequest.PERI in exc.value.missing_services


# --- Capability gating ---


def test_arm_night_rejected_without_armnight_service() -> None:
    """ARMNIGHT is a reliably-reported sub-capability; its absence gates."""
    services = frozenset({ServiceRequest.ARM, ServiceRequest.DARM})
    r = _r("SDVECU", services, _PARTITIONS_WITH_PERIMETER)
    target_night = AlarmState(
        interior=InteriorMode.TOTAL,
        perimeter=PerimeterMode.OFF,
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

    Provide a positive partition tuple so the perimeter family-gate
    (which would otherwise fire first on a demoted install) is
    satisfied; we want the test to land on the missing-ARM check.
    """
    services = frozenset({ServiceRequest.DARM})
    r = _r("SDVECU", services, _PARTITIONS_WITH_PERIMETER)
    with pytest.raises(UnsupportedCommandError) as exc:
        r.resolve(target=_TOTAL_PERI, current=_OFF)
    assert ServiceRequest.ARM in exc.value.missing_services


# --- Degenerate ---


def test_noop_when_target_equals_current() -> None:
    from verisure_italy.exceptions import SameStateError

    r = _r("SDVECU", _SDVECU_SERVICES, _PARTITIONS_WITH_PERIMETER)
    with pytest.raises(SameStateError, match="already in target state"):
        r.resolve(target=_OFF, current=_OFF)


def test_unknown_panel_rejected() -> None:
    r = _r("TOTALLY_FAKE", _SDVECU_SERVICES, ())
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
    r = _r("SDVECU", _SDVECU_SERVICES, _PARTITIONS_WITH_PERIMETER)
    with pytest.raises(ValueError, match="Cross-perimeter armed transition"):
        r.resolve(target=_PARTIAL, current=_TOTAL_PERI)


def test_cross_peri_from_partial_peri_to_total_rejected() -> None:
    r = _r("SDVECU", _SDVECU_SERVICES, _PARTITIONS_WITH_PERIMETER)
    with pytest.raises(ValueError, match="Cross-perimeter armed transition"):
        r.resolve(target=_TOTAL, current=_PARTIAL_PERI)


def test_cross_peri_from_peri_only_to_total_does_not_trigger() -> None:
    """current=OFF/ON is NOT an armed interior; this branch must NOT fire.

    Going from perimeter-only (interior OFF) to total arm is a
    normal arm-from-disarmed path; returns ARM_TOTAL.
    """
    r = _r("SDVECU", _SDVECU_SERVICES, _PARTITIONS_WITH_PERIMETER)
    out = r.resolve(target=_TOTAL, current=_PERI)
    assert out == ArmCommand.ARM_TOTAL


# --- v0.9.4: Partition-aware perimeter gate (Issue #5) ---
#
# Supersedes the v0.9.3 EST-based gate. The gate now reads partition 02
# enterStates: if empty, effective_family() demotes PERI_CAPABLE → INTERIOR_ONLY.
# Diagnostic message names the partition-permission gap, not EST.


class TestResolverPerimeterGate:
    """Partition gate for arm/disarm — supersedes v0.9.3 EST-based gate."""

    def test_sdvecu_with_perimeter_picks_armday1peri1(self) -> None:
        resolver = CommandResolver(
            panel="SDVECU",
            active_services=frozenset(
                {
                    ServiceRequest.ARM,
                    ServiceRequest.DARM,
                    ServiceRequest.ARMNIGHT,
                }
            ),
            alarm_partitions=_PARTITIONS_WITH_PERIMETER,
        )

        cmd = resolver.resolve(
            target=AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON),
            current=AlarmState(interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF),
        )

        assert cmd == ArmCommand.ARM_PARTIAL_PERIMETER

    def test_sdvecu_without_perimeter_demotes_to_armday1(self) -> None:
        """laurafabry profile — partition 02 empty -> INTERIOR_ONLY effective family."""
        resolver = CommandResolver(
            panel="SDVECU",
            active_services=frozenset(
                {
                    ServiceRequest.ARM,
                    ServiceRequest.DARM,
                    ServiceRequest.ARMNIGHT,
                }
            ),
            alarm_partitions=_PARTITIONS_WITHOUT_PERIMETER,
        )

        # Caller (HA entity) would not request a PERIMETER target on a demoted
        # install — but the resolver-layer guard exists for direct service callers.
        with pytest.raises(UnsupportedCommandError) as exc_info:
            resolver.resolve(
                target=AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON),
                current=AlarmState(interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF),
            )

        # The diagnostic must NOT name EST; it names the missing per-user
        # permission via partition. Assert the new-shape contract directly
        # (detail string + empty missing_services) — substring matches on
        # str(exc) are too loose because the command name itself contains
        # "perimeter".
        assert exc_info.value.missing_services == frozenset()
        assert exc_info.value.detail is not None
        assert "partition" in exc_info.value.detail.lower()

    def test_sdvecu_without_perimeter_arm_partial_off_works(self) -> None:
        """Demoted install picks the interior-only command, no exception."""
        resolver = CommandResolver(
            panel="SDVECU",
            active_services=frozenset(
                {
                    ServiceRequest.ARM,
                    ServiceRequest.DARM,
                    ServiceRequest.ARMNIGHT,
                }
            ),
            alarm_partitions=_PARTITIONS_WITHOUT_PERIMETER,
        )

        cmd = resolver.resolve(
            target=AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.OFF),
            current=AlarmState(interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF),
        )

        assert cmd == ArmCommand.ARM_PARTIAL

    def test_sdvfast_missing_perimeter_reports_peri_not_partition(self) -> None:
        """Model-level INTERIOR_ONLY (no perimeter hardware) reports PERI missing.

        Distinguishes: PERI_CAPABLE-no-partition = partition detail message;
        INTERIOR_ONLY = missing PERI (model has no perimeter hardware at all).
        Issue body should point at the right diagnostic.
        """
        resolver = CommandResolver(
            panel="SDVFAST",
            active_services=_SDVFAST_SERVICES,
            alarm_partitions=(),
        )
        with pytest.raises(UnsupportedCommandError) as exc:
            resolver.resolve(target=_TOTAL_PERI, current=_OFF)
        assert ServiceRequest.PERI in exc.value.missing_services
        # Model-level INTERIOR_ONLY: no detail string (service-flag path).
        assert exc.value.detail is None
