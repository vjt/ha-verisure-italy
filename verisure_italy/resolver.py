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

from pydantic import BaseModel, ConfigDict

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

# Every command → the base service(s) that must be active to honour it.
#
# Empirical observation on a live SDVECU panel: `xSSrv` lists ARM + DARM +
# ARMNIGHT as active but does NOT list ARMDAY or PERI, yet the panel
# accepts ARMDAY1 / ARM1PERI1 / ARMDAY1PERI1 / DARM1DARMPERI fine. The
# `ARMDAY` and `PERI` service rows are UI capability hints ("show the
# Day and Perimeter buttons?"), not hard gates on the wire protocol.
#
# Mapping rule: every arm variant requires `ARM`; every disarm variant
# requires `DARM`. Sub-capabilities that ARE reliably reported per-panel:
#   - ARMNIGHT (SDVECU active; absent on panels without night mode)
#   - ARMANNEX / DARMANNEX (annex-equipped panels only)
# Perimeter gating is handled by panel FAMILY below (PERI_CAPABLE vs
# INTERIOR_ONLY) — not by the ServiceRequest.PERI flag, which isn't
# reliable across panels.
_COMMAND_REQUIRES: dict[ArmCommand, frozenset[ServiceRequest]] = {
    # Disarm — base DARM only; perimeter-disarm variants are family-gated.
    ArmCommand.DISARM: frozenset({ServiceRequest.DARM}),
    ArmCommand.DISARM_ALL: frozenset({ServiceRequest.DARM}),
    ArmCommand.DISARM_PERIMETER: frozenset({ServiceRequest.DARM}),
    ArmCommand.DISARM_ANNEX: frozenset({ServiceRequest.DARM, ServiceRequest.DARMANNEX}),
    # Arm — base ARM always required; NIGHT + ANNEX are observed sub-caps.
    ArmCommand.ARM_TOTAL: frozenset({ServiceRequest.ARM}),
    ArmCommand.ARM_TOTAL_PERIMETER: frozenset({ServiceRequest.ARM}),
    ArmCommand.ARM_PARTIAL: frozenset({ServiceRequest.ARM}),
    ArmCommand.ARM_PARTIAL_PERIMETER: frozenset({ServiceRequest.ARM}),
    ArmCommand.ARM_NIGHT: frozenset({ServiceRequest.ARM, ServiceRequest.ARMNIGHT}),
    ArmCommand.ARM_NIGHT_PERIMETER: frozenset({ServiceRequest.ARM, ServiceRequest.ARMNIGHT}),
    # Single-step transition commands — only SDVFAST-family panels expose
    # the ARMINTFPART / ARMPARTFINT services. SDVECU responds
    # "Request ARMPARTFINTDAY1 is not valid for Central Unit" if sent
    # blind; hence the explicit sub-capability gate.
    ArmCommand.ARM_TOTAL_FROM_ARMED_INTERIOR: frozenset({
        ServiceRequest.ARM, ServiceRequest.ARMINTFPART,
    }),
    ArmCommand.ARM_PARTIAL_FROM_TOTAL: frozenset({
        ServiceRequest.ARM, ServiceRequest.ARMPARTFINT,
    }),
    ArmCommand.ARM_NIGHT_FROM_TOTAL: frozenset({
        ServiceRequest.ARM, ServiceRequest.ARMPARTFINT, ServiceRequest.ARMNIGHT,
    }),
    ArmCommand.ARM_ANNEX: frozenset({ServiceRequest.ARM, ServiceRequest.ARMANNEX}),
    ArmCommand.ARM_INTERIOR_EXTERIOR: frozenset({ServiceRequest.ARM}),
    ArmCommand.ARM_PERIMETER: frozenset({ServiceRequest.ARM}),
}

# Commands that operate on the perimeter axis — rejected client-side on
# panels in the INTERIOR_ONLY family regardless of service declarations.
# Source of truth: web-bundle panel classifier R(e). See
# docs/findings/arm-command-vocabulary.md.
_PERI_COMMANDS: frozenset[ArmCommand] = frozenset({
    ArmCommand.ARM_PERIMETER,
    ArmCommand.ARM_TOTAL_PERIMETER,
    ArmCommand.ARM_PARTIAL_PERIMETER,
    ArmCommand.ARM_NIGHT_PERIMETER,
    ArmCommand.ARM_INTERIOR_EXTERIOR,
    ArmCommand.DISARM_ALL,
    ArmCommand.DISARM_PERIMETER,
})


class CommandResolver(BaseModel):
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

    model_config = ConfigDict(frozen=True)

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

        # Armed → armed interior-mode transition (same perimeter, different
        # interior). Single-step transition commands only work on panels
        # that expose ARMINTFPART / ARMPARTFINT as active services
        # (SDVFAST family). SDVECU rejects ARMPARTFINTDAY1 etc. with
        # "Request not valid for Central Unit". For SDVECU we fall
        # through to the normal arm-from-disarmed command below — this
        # matches the pre-v0.9.0 behaviour that worked in production for
        # months (target-only lookup via the old STATE_TO_COMMAND dict).
        if (
            current.interior != InteriorMode.OFF
            and current.interior != target.interior
            and current.perimeter == target.perimeter
        ):
            if (
                target.interior == InteriorMode.TOTAL
                and ServiceRequest.ARMINTFPART in self.active_services
            ):
                return ArmCommand.ARM_TOTAL_FROM_ARMED_INTERIOR
            if (
                current.interior == InteriorMode.TOTAL
                and target.interior == InteriorMode.PARTIAL
                and ServiceRequest.ARMPARTFINT in self.active_services
            ):
                return ArmCommand.ARM_PARTIAL_FROM_TOTAL
            if (
                current.interior == InteriorMode.TOTAL
                and ServiceRequest.ARMPARTFINT in self.active_services
            ):
                return ArmCommand.ARM_NIGHT_FROM_TOTAL
            # Otherwise fall through to the arm-from-disarmed match block.

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
        """Raise UnsupportedCommandError if the panel cannot honour command.

        Two gates, both fail-secure:
          1. Family — INTERIOR_ONLY panels reject every perimeter variant
             (PERI service flags aren't universally reliable; family is).
          2. Sub-capability — ARMNIGHT / ARMANNEX / DARMANNEX gates based
             on Service.active. Base ARM / DARM are always required.
        """
        family = PANEL_FAMILIES[self.panel]
        if command in _PERI_COMMANDS and family == PanelFamily.INTERIOR_ONLY:
            raise UnsupportedCommandError(
                command=command,
                panel=self.panel,
                missing_services=frozenset({ServiceRequest.PERI}),
            )
        required = _COMMAND_REQUIRES[command]
        missing = required - self.active_services
        if missing:
            raise UnsupportedCommandError(
                command=command,
                panel=self.panel,
                missing_services=frozenset(missing),
            )
