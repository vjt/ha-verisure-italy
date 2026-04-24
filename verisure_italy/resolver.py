"""Command resolver — decides which ArmCommand reaches a target alarm state.

Mirrors the decoded Verisure web-app resolver documented in
docs/findings/arm-command-vocabulary.md. Each panel has:
  - a family (peri-capable vs not) determining state space
  - a set of active services (from xSSrv) determining which
    enum members it actually honours

The resolver is pure — no IO. `client.arm()` / `client.disarm()`
construct one per operation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .exceptions import UnsupportedCommandError
from .models import (
    PANEL_FAMILIES,
    AlarmState,
    ArmCommand,
    InteriorMode,
    PanelFamily,
    PerimeterMode,
    ServiceRequest,
)

# Every command → the service(s) that must be active to honour it.
# Expressed as "all of these must be active" semantics — most commands
# need exactly one, compound modes need two (the base service AND PERI).
_COMMAND_REQUIRES: dict[ArmCommand, frozenset[ServiceRequest]] = {
    ArmCommand.DISARM: frozenset({ServiceRequest.DARM}),
    ArmCommand.DISARM_ALL: frozenset({ServiceRequest.DARM, ServiceRequest.PERI}),
    ArmCommand.DISARM_PERIMETER: frozenset({ServiceRequest.PERI}),
    ArmCommand.DISARM_ANNEX: frozenset({ServiceRequest.DARMANNEX}),
    ArmCommand.ARM_TOTAL: frozenset({ServiceRequest.ARM}),
    ArmCommand.ARM_TOTAL_PERIMETER: frozenset({ServiceRequest.ARM, ServiceRequest.PERI}),
    ArmCommand.ARM_PARTIAL: frozenset({ServiceRequest.ARMDAY}),
    ArmCommand.ARM_PARTIAL_PERIMETER: frozenset({ServiceRequest.ARMDAY, ServiceRequest.PERI}),
    ArmCommand.ARM_NIGHT: frozenset({ServiceRequest.ARMNIGHT}),
    ArmCommand.ARM_NIGHT_PERIMETER: frozenset({ServiceRequest.ARMNIGHT, ServiceRequest.PERI}),
    # Transition commands are gated by the base service that unlocks them
    # per the web bundle table (ARM → ARMINTFPART1; ARMDAY → ARMPARTFINT*1).
    # Panels that expose ARMINTFPART / ARMPARTFINT as *standalone* services
    # (e.g. SDVFAST) satisfy this check via ARM / ARMDAY respectively —
    # both sets appear in their active_services.
    ArmCommand.ARM_TOTAL_FROM_ARMED_INTERIOR: frozenset({ServiceRequest.ARM}),
    ArmCommand.ARM_PARTIAL_FROM_TOTAL: frozenset({ServiceRequest.ARMDAY}),
    ArmCommand.ARM_NIGHT_FROM_TOTAL: frozenset({ServiceRequest.ARMDAY}),
    ArmCommand.ARM_ANNEX: frozenset({ServiceRequest.ARMANNEX}),
    ArmCommand.ARM_INTERIOR_EXTERIOR: frozenset({ServiceRequest.ARM, ServiceRequest.PERI}),
    ArmCommand.ARM_PERIMETER: frozenset({ServiceRequest.PERI}),
}


@dataclass(frozen=True)
class CommandResolver:
    """Decide which ArmCommand reaches the target from the current state.

    Covers the interior x perimeter axis only. Out of scope:
      - annex arm/disarm (ARM_ANNEX, DISARM_ANNEX)
      - perimeter-only disarm (DISARM_PERIMETER)
      - Spain-WAF-safe ARMINTEXT1 alias (ARM_INTERIOR_EXTERIOR)
      - night mode (ARM_NIGHT, ARM_NIGHT_PERIMETER, ARM_NIGHT_FROM_TOTAL)
        — InteriorMode has no NIGHT variant today; entries are parked
        in `_COMMAND_REQUIRES` for future expansion.

    These are routed by the caller with an explicit ArmCommand
    argument, not derived from AlarmState transitions.
    """

    panel: str
    active_services: frozenset[ServiceRequest]

    def resolve(self, *, target: AlarmState, current: AlarmState) -> ArmCommand:
        """Return the ArmCommand for current -> target.

        Raises ValueError (bad transition) or UnsupportedCommandError (missing service).
        """
        if self.panel not in PANEL_FAMILIES:
            raise ValueError(f"Unknown panel {self.panel!r}")
        if target == current:
            raise ValueError("current == target — no command needed")

        family = PANEL_FAMILIES[self.panel]
        command = self._pick_command(target=target, current=current, family=family)
        self._assert_supported(command)
        return command

    # --- private ---

    def _pick_command(
        self,
        *,
        target: AlarmState,
        current: AlarmState,
        family: PanelFamily,
    ) -> ArmCommand:
        """Return the ArmCommand for target; raises ValueError if the transition is unsupported."""
        # Disarm entirely
        if target.interior == InteriorMode.OFF and target.perimeter == PerimeterMode.OFF:
            if current.perimeter == PerimeterMode.ON:
                return ArmCommand.DISARM_ALL
            return ArmCommand.DISARM

        # Perimeter-only arm (peri-capable only)
        if target.interior == InteriorMode.OFF and target.perimeter == PerimeterMode.ON:
            return ArmCommand.ARM_PERIMETER

        # Reject cross-perimeter armed transitions explicitly.
        # The web resolver keys transitions on interior mode alone;
        # perimeter flips from an armed state aren't a defined
        # single-command transition. Caller must disarm first.
        if (
            current.interior != InteriorMode.OFF
            and current.perimeter != target.perimeter
        ):
            raise ValueError(
                f"Cross-perimeter armed transition not supported: "
                f"current={current}, target={target}. "
                f"Disarm first, then arm to the target state."
            )

        # Armed → armed interior-mode transition (same-side transitions)
        if (
            current.interior != InteriorMode.OFF
            and current.interior != target.interior
            and current.perimeter == target.perimeter
        ):
            if target.interior == InteriorMode.TOTAL:
                return ArmCommand.ARM_TOTAL_FROM_ARMED_INTERIOR
            if current.interior == InteriorMode.TOTAL and target.interior == InteriorMode.PARTIAL:
                return ArmCommand.ARM_PARTIAL_FROM_TOTAL
            if current.interior == InteriorMode.TOTAL:  # PARTIAL case already handled
                return ArmCommand.ARM_NIGHT_FROM_TOTAL

        # Arm from disarmed
        match (target.interior, target.perimeter):
            case (InteriorMode.TOTAL, PerimeterMode.ON):
                return ArmCommand.ARM_TOTAL_PERIMETER
            case (InteriorMode.TOTAL, PerimeterMode.OFF):
                return ArmCommand.ARM_TOTAL
            case (InteriorMode.PARTIAL, PerimeterMode.ON):
                return ArmCommand.ARM_PARTIAL_PERIMETER
            case (InteriorMode.PARTIAL, PerimeterMode.OFF):
                return ArmCommand.ARM_PARTIAL
            case _:
                # InteriorMode.OFF paths are handled by the early returns above.
                # This branch is unreachable in practice; the raise below fires
                # for truly unexpected combinations.
                pass
        raise ValueError(
            f"No command for target={target} current={current} family={family}"
        )

    def _assert_supported(self, command: ArmCommand) -> None:
        """Raise UnsupportedCommandError if command requires a service not in active_services."""
        required = _COMMAND_REQUIRES[command]
        missing = required - self.active_services
        if missing:
            raise UnsupportedCommandError(
                command=command,
                panel=self.panel,
                missing_services=frozenset(missing),
            )
