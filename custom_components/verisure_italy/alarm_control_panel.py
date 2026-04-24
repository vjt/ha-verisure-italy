"""Alarm control panel entity for Verisure Italy."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
)
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pydantic import ValidationError

from verisure_italy import (
    OperationFailedError,
    OperationTimeoutError,
    SameStateError,
    UnsupportedPanelError,
    VerisureError,
    run_probe,
)
from verisure_italy.exceptions import ArmingExceptionError
from verisure_italy.models import (
    PROTO_TO_STATE,
    SUPPORTED_PANELS,
    AlarmState,
    InteriorMode,
    PerimeterMode,
    ProtoCode,
)

from . import VerisureConfigEntry
from .const import DOMAIN
from .coordinator import ForceArmContext, VerisureCoordinator

_LOGGER = logging.getLogger(__name__)

# Canonical target states for arm actions
_PARTIAL_PERIMETER = AlarmState(
    interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON
)
_TOTAL_PERIMETER = AlarmState(
    interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON
)
_DISARMED = AlarmState(
    interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF
)

# Map our AlarmState to HA AlarmControlPanelState
_STATE_MAP: dict[AlarmState, AlarmControlPanelState] = {
    _DISARMED: AlarmControlPanelState.DISARMED,
    _PARTIAL_PERIMETER: AlarmControlPanelState.ARMED_HOME,
    _TOTAL_PERIMETER: AlarmControlPanelState.ARMED_AWAY,
    PROTO_TO_STATE[ProtoCode.PERIMETER_ONLY]: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
    PROTO_TO_STATE[ProtoCode.PARTIAL]: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
    PROTO_TO_STATE[ProtoCode.TOTAL]: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
}

# Startup assertion: _STATE_MAP must cover all PROTO_TO_STATE values
assert _STATE_MAP.keys() == set(PROTO_TO_STATE.values()), (
    f"_STATE_MAP keys {set(_STATE_MAP.keys())} don't match "
    f"PROTO_TO_STATE values {set(PROTO_TO_STATE.values())}"
)

_NOTIFICATION_ID_PREFIX = f"{DOMAIN}.arming_exception"

# Persistent-notification body shown when arm/disarm fails. Points users at
# the BEGIN/END marker block emitted by verisure_italy.client._log_failure —
# pasting that block into a GitHub issue gives the maintainer a
# fully-diagnosable, PII-safe trace without any further back-and-forth.
_OPERATION_FAILED_NOTIFICATION_TEMPLATE = (
    "{operation} failed: {message}\n\n"
    "For help, open a new issue at\n"
    "https://github.com/vjt/ha-verisure-italy/issues\n"
    "and paste the block between\n"
    "`=== VERISURE {op_upper} FAILURE BEGIN ===` and\n"
    "`=== VERISURE {op_upper} FAILURE END ===`\n"
    "from your Home Assistant log."
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VerisureConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the alarm control panel from a config entry."""
    coordinator = config_entry.runtime_data
    async_add_entities([VerisureAlarmPanel(coordinator)])


# Ignore justification — CoordinatorEntity and AlarmControlPanelEntity both
# declare _attr_* class vars with variance-incompatible types. C3 linearization
# resolves the override correctly at runtime; pyright strict cannot see it.
class VerisureAlarmPanel(  # type: ignore[reportIncompatibleVariableOverride]
    CoordinatorEntity[VerisureCoordinator], AlarmControlPanelEntity
):
    """Alarm control panel for Verisure Italy."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:shield-home"
    _attr_name = "Alarm"
    _attr_code_arm_required = False

    def __init__(self, coordinator: VerisureCoordinator) -> None:
        super().__init__(coordinator)
        inst = coordinator.installation
        self._attr_unique_id = f"{DOMAIN}_{inst.number}_alarm"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, inst.number)},
            name="Verisure",
            manufacturer="Verisure Italy",
            model=f"{inst.panel} — {inst.alias}",
        )
        coordinator.alarm_entity = self
        self._arm_lock = asyncio.Lock()
        self._force_context_timer: CALLBACK_TYPE | None = None
        self._update_alarm_state()

    def _update_alarm_state(self) -> None:
        """Update _attr_alarm_state from coordinator data. Crashes on unknown state."""
        self._attr_alarm_state = _STATE_MAP[self.coordinator.data.alarm_state]
        self._update_supported_features()
        self._update_force_attributes()

    def _update_supported_features(self) -> None:
        """Expose arm modes only when currently disarmed.

        Verisure panels reject armed -> armed interior transitions
        (arm_home -> arm_away etc.) with error_code 106 / "Request not
        valid for Central Unit". The Verisure mobile app enforces the
        same rule by graying out the arm buttons while armed. Mirror
        that in HA: when already armed, the UI shows only the disarm
        action; the user must disarm first to change mode.

        Pops the cached_property from the instance __dict__ so HA
        re-reads _attr_supported_features on the next state write.
        """
        if self._attr_alarm_state == AlarmControlPanelState.DISARMED:
            self._attr_supported_features = (
                AlarmControlPanelEntityFeature.ARM_HOME
                | AlarmControlPanelEntityFeature.ARM_AWAY
            )
        else:
            self._attr_supported_features = AlarmControlPanelEntityFeature(0)
        vars(self).pop("supported_features", None)

    def _update_force_attributes(self) -> None:
        """Update extra state attributes from force context."""
        ctx = self.coordinator.force_context
        if ctx is not None:
            self._attr_extra_state_attributes = {
                "force_arm_available": True,
                "arm_exceptions": [e.alias for e in ctx.exceptions],
            }
        else:
            self._attr_extra_state_attributes = {}

    def _handle_coordinator_update(self) -> None:
        """Sync alarm state from coordinator."""
        if self._arm_lock.locked():
            # Arm/disarm API call running — state already set explicitly,
            # don't write it again (avoids spurious state_changed events).
            return

        # Clear stale force-arm context if the panel has moved away from
        # DISARMED through any other path (user armed via the Verisure
        # mobile app, another HA automation, an SMS-triggered arm, etc.)
        # while our 120s context timer is still live. The stored
        # reference_id / suid is scoped to that specific pending-arm
        # attempt; once the panel is armed, pressing "Force Arm" with a
        # stale token fails with an opaque error. Clearing now is the
        # right UX: the arming goal has been achieved by another path.
        if (
            self.coordinator.force_context is not None
            and self.coordinator.data.alarm_state != _DISARMED
        ):
            _LOGGER.info(
                "Clearing stale force-arm context — panel no longer disarmed"
            )
            self._clear_force_context()
            self.hass.async_create_task(
                self._dismiss_notification(),
                "verisure_italy_dismiss_stale_force_notification",
            )

        # Always show the real panel state — even during force-arm.
        # The force-arm pending status is communicated through
        # extra_state_attributes, buttons, and notifications.
        self._update_alarm_state()
        super()._handle_coordinator_update()

    async def _check_panel_supported(self, action: str) -> bool:
        """Guard arm/disarm against unverified panel types.

        Returns True when safe to proceed. Returns False after emitting
        a probe to the log and notifying the user — the caller must
        bail out without sending any command.
        """
        panel = self.coordinator.installation.panel
        if panel in SUPPORTED_PANELS:
            return True

        _LOGGER.warning(
            "Refusing %s: panel %r not in SUPPORTED_PANELS=%s. "
            "Running read-only probe for diagnosis.",
            action, panel, sorted(SUPPORTED_PANELS),
        )
        try:
            probe = await run_probe(
                self.coordinator.client, self.coordinator.installation,
            )
            _LOGGER.warning(
                "=== VERISURE PROBE BEGIN ===\n%s\n=== VERISURE PROBE END ===",
                json.dumps(probe, indent=2, sort_keys=True),
            )
        except Exception:
            # Probe failure MUST NOT mask the gate: we still raise
            # UnsupportedPanelError below. Any probe crash (network, pydantic,
            # programming error) is logged for triage and swallowed so the
            # refusal path stays reachable. Narrowing here would re-introduce
            # the risk of an unsupported panel slipping past the gate.
            _LOGGER.exception("Probe failed for unsupported panel %r", panel)

        await self._notify_unsupported_panel(panel, action)
        raise UnsupportedPanelError(
            panel,
            f"Panel type {panel!r} is not yet supported. "
            f"Search github.com/vjt/ha-verisure-italy/issues for your panel "
            f"or open a new issue with the probe output from the HA log "
            f"(marked 'VERISURE PROBE BEGIN').",
        )

    async def _notify_unsupported_panel(self, panel: str, action: str) -> None:
        """User-facing notification when the panel is not on the allowlist."""
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "message": (
                    f"{action.capitalize()} refused: panel type '{panel}' is "
                    f"not verified. Zero bytes were sent to the panel. "
                    f"A diagnostic probe was written to the HA log (search "
                    f"for 'VERISURE PROBE BEGIN'). Please check "
                    f"[GitHub issues](https://github.com/vjt/ha-verisure-italy/issues) "
                    f"for your panel type, or open a new issue and paste the "
                    f"probe between the BEGIN/END markers."
                ),
                "title": "Verisure Italy — Unsupported Panel",
                "notification_id": f"{DOMAIN}.unsupported_panel",
            },
        )

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm partial + perimeter."""
        await self._async_arm(_PARTIAL_PERIMETER, AlarmControlPanelState.ARMED_HOME, "armed_home")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm total + perimeter."""
        await self._async_arm(_TOTAL_PERIMETER, AlarmControlPanelState.ARMED_AWAY, "armed_away")

    async def _async_arm(
        self,
        target: AlarmState,
        ha_state: AlarmControlPanelState,
        mode: str,
    ) -> None:
        """Execute arm operation with force-arm exception handling."""
        if self._attr_alarm_state == ha_state:
            _LOGGER.debug("Arm-to-%s ignored — already in that state", ha_state)
            return

        # Armed -> armed interior transitions (arm_home -> arm_away etc.)
        # are rejected by the panel with error_code 106. The UI already
        # hides the buttons via `supported_features`; this guard catches
        # direct service calls (automations, REST) that bypass the UI.
        if self._attr_alarm_state != AlarmControlPanelState.DISARMED:
            _LOGGER.warning(
                "Arm-to-%s refused — panel already armed (%s). "
                "Disarm first to change mode.",
                ha_state, self._attr_alarm_state,
            )
            return

        if self._arm_lock.locked():
            _LOGGER.warning("Arm rejected — another operation in progress")
            return

        if not await self._check_panel_supported("arm"):
            return  # _check_panel_supported raises; defensive belt-and-braces

        async with self._arm_lock, self.coordinator.suppress_updates():
            _LOGGER.info("Arming %s (target: %s)", mode, target)
            self._attr_alarm_state = AlarmControlPanelState.ARMING
            self.async_write_ha_state()

            try:
                await self.coordinator.async_arm(target)
            except SameStateError:
                # Benign no-op: panel already in target state (race the
                # entity's `_attr_alarm_state` check lost against a poll
                # that already observed the target). With M3 the window
                # is closed for background polls, but a direct service
                # call that bypassed the entity guard could still hit it.
                _LOGGER.info(
                    "Arm-to-%s is a no-op — panel already in target state",
                    mode,
                )
                self._update_alarm_state()
                self.async_write_ha_state()
                return
            except ArmingExceptionError as exc:
                zones = ", ".join(e.alias for e in exc.exceptions)
                _LOGGER.warning(
                    "Arming blocked by %d open zone(s): %s",
                    len(exc.exceptions), zones,
                )
                # Revert to real panel state — the alarm is still disarmed.
                # Force-arm status communicated via attributes + dashboard.
                self._update_alarm_state()
                self._set_force_context(exc, mode, target)
                await self._notify_arm_exceptions(exc)
                self._fire_arming_exception_event(exc, mode)
                self.async_write_ha_state()
                return
            except (OperationFailedError, OperationTimeoutError) as exc:
                self._update_alarm_state()
                _LOGGER.error("Arm failed: %s", exc.message)
                await self._notify_operation_failed(
                    "Arm", exc.message, marker_operation="ARM",
                )
                self.async_write_ha_state()
                return
            except (VerisureError, ValidationError) as exc:
                self._update_alarm_state()
                msg = exc.message if isinstance(exc, VerisureError) else str(exc)
                _LOGGER.error("Arm failed (unexpected): %s", msg)
                await self._notify_operation_failed(
                    "Arm", msg, marker_operation="ARM",
                )
                self.async_write_ha_state()
                return

        _LOGGER.info("Armed %s successfully", mode)
        self._expire_force_context()
        await self.coordinator.async_request_refresh()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the alarm."""
        if self._attr_alarm_state == AlarmControlPanelState.DISARMED:
            _LOGGER.debug("Disarm ignored — already disarmed")
            return

        if self._arm_lock.locked():
            _LOGGER.warning("Disarm rejected — another operation in progress")
            return

        if not await self._check_panel_supported("disarm"):
            return

        async with self._arm_lock, self.coordinator.suppress_updates():
            _LOGGER.info("Disarming alarm")
            self._attr_alarm_state = AlarmControlPanelState.DISARMING
            self.async_write_ha_state()

            try:
                await self.coordinator.async_disarm()
            except SameStateError:
                _LOGGER.info("Disarm is a no-op — panel already disarmed")
                self._update_alarm_state()
                self.async_write_ha_state()
                return
            except (OperationFailedError, OperationTimeoutError) as exc:
                self._update_alarm_state()
                _LOGGER.error("Disarm failed: %s", exc.message)
                await self._notify_operation_failed(
                    "Disarm", exc.message, marker_operation="DISARM",
                )
                self.async_write_ha_state()
                return
            except (VerisureError, ValidationError) as exc:
                self._update_alarm_state()
                msg = exc.message if isinstance(exc, VerisureError) else str(exc)
                _LOGGER.error("Disarm failed (unexpected): %s", msg)
                await self._notify_operation_failed(
                    "Disarm", msg, marker_operation="DISARM",
                )
                self.async_write_ha_state()
                return

        _LOGGER.info("Disarmed successfully")
        self._expire_force_context()
        await self.coordinator.async_request_refresh()

    # --- Force arm ---

    async def async_force_arm(self) -> None:
        """Force-arm using stored exception context."""
        ctx = self.coordinator.force_context
        if ctx is None:
            _LOGGER.warning("force_arm called but no force context available")
            return

        if self._arm_lock.locked():
            _LOGGER.warning("Force arm rejected — another operation in progress")
            return

        zones = ", ".join(e.alias for e in ctx.exceptions)

        async with self._arm_lock, self.coordinator.suppress_updates():
            _LOGGER.info(
                "Force-arming with %d bypassed zone(s): %s",
                len(ctx.exceptions), zones,
            )
            await self._dismiss_notification()
            self.async_write_ha_state()

            try:
                await self.coordinator.async_arm(
                    ctx.target,
                    force_arming_remote_id=ctx.reference_id,
                    suid=ctx.suid,
                )
            except SameStateError:
                # Panel already in target state — treat as success.
                _LOGGER.info(
                    "Force-arm is a no-op — panel already in target state"
                )
                self._clear_force_context()
                self._update_alarm_state()
                self.async_write_ha_state()
                return
            except (OperationFailedError, OperationTimeoutError) as exc:
                self._clear_force_context()
                self._update_alarm_state()
                _LOGGER.error("Force arm failed: %s", exc.message)
                # Client-level operation is still "arm" — the marker block
                # in the log reads VERISURE ARM FAILURE BEGIN, not "FORCE ARM".
                await self._notify_operation_failed(
                    "Force arm", exc.message, marker_operation="ARM",
                )
                self.async_write_ha_state()
                return
            except (VerisureError, ValidationError) as exc:
                self._clear_force_context()
                self._update_alarm_state()
                msg = exc.message if isinstance(exc, VerisureError) else str(exc)
                _LOGGER.error("Force arm failed (unexpected): %s", msg)
                await self._notify_operation_failed(
                    "Force arm", msg, marker_operation="ARM",
                )
                self.async_write_ha_state()
                return

        _LOGGER.info("Force-armed successfully, bypassed: %s", zones)
        self._expire_force_context()
        await self.coordinator.async_request_refresh()

    async def async_force_arm_cancel(self) -> None:
        """Cancel pending force-arm context."""
        if self.coordinator.force_context is None:
            _LOGGER.warning("force_arm_cancel called but no context")
            return

        _LOGGER.info("Force-arm cancelled by user")
        self._clear_force_context()
        self._update_alarm_state()
        await self._dismiss_notification()
        self.async_write_ha_state()

    def _set_force_context(
        self,
        exc: ArmingExceptionError,
        mode: str,
        target: AlarmState,
    ) -> None:
        """Store force-arm context from an arming exception.

        Starts a 120-second timer for deterministic expiry, independent
        of poll interval.
        """
        self._cancel_force_context_timer()
        self.coordinator.force_context = ForceArmContext(
            reference_id=exc.reference_id,
            suid=exc.suid,
            mode=mode,
            target=target,
            exceptions=exc.exceptions,
            created_at=datetime.datetime.now(datetime.UTC),
        )
        self._update_force_attributes()
        self.coordinator.async_update_listeners()

        async def _async_expire_force_context() -> None:
            if self.coordinator.force_context is None:
                return  # already cleared by user action
            _LOGGER.info("Force context expired after 2 minutes")
            self.coordinator.force_context = None
            self._force_context_timer = None
            self._update_force_attributes()
            self._update_alarm_state()
            self.async_write_ha_state()
            self.coordinator.async_update_listeners()
            await self._dismiss_notification()

        def _on_force_context_expired(_now: datetime.datetime) -> None:
            self.hass.async_create_task(
                _async_expire_force_context(),
                "verisure_italy_expire_force_context",
            )

        self._force_context_timer = async_call_later(
            self.hass, 120, _on_force_context_expired
        )

    def _cancel_force_context_timer(self) -> None:
        """Cancel the force context expiry timer if active."""
        if self._force_context_timer is not None:
            self._force_context_timer()
            self._force_context_timer = None

    def _clear_force_context(self) -> None:
        """Clear force context and notify all entities immediately.

        Use in cancel/error paths where we want buttons to disappear NOW.
        """
        if self.coordinator.force_context is None:
            return
        self._cancel_force_context_timer()
        self.coordinator.force_context = None
        self._update_force_attributes()
        self.coordinator.async_update_listeners()

    def _expire_force_context(self) -> None:
        """Clear force context silently — no listener notification.

        Use in success paths where async_request_refresh() will notify
        all entities with fresh data (avoids spurious state transitions
        from stale coordinator data).
        """
        if self.coordinator.force_context is None:
            return
        self._cancel_force_context_timer()
        self.coordinator.force_context = None
        self._update_force_attributes()

    def _notification_id(self) -> str:
        return (
            f"{_NOTIFICATION_ID_PREFIX}"
            f"_{self.coordinator.installation.number}"
        )

    async def _notify_arm_exceptions(self, exc: ArmingExceptionError) -> None:
        """Create a persistent notification about open zones."""
        zone_list = ", ".join(e.alias for e in exc.exceptions)
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "message": f"Arming blocked by open zones: {zone_list}",
                "title": "Verisure Italy — Open Zones",
                "notification_id": self._notification_id(),
            },
        )

    async def _dismiss_notification(self) -> None:
        """Dismiss the arming exception notification."""
        await self.hass.services.async_call(
            "persistent_notification",
            "dismiss",
            {"notification_id": self._notification_id()},
        )

    async def _notify_operation_failed(
        self,
        operation: str,
        message: str,
        *,
        marker_operation: str,
    ) -> None:
        """Create a persistent notification about a failed operation.

        `operation` is the human-facing label ("Arm", "Disarm", "Force arm").
        `marker_operation` is the uppercase client-level operation the user
        will see inside the log ("ARM" or "DISARM"). They differ for "Force
        arm" — the client-level block still says VERISURE ARM FAILURE BEGIN
        because the underlying client call is `arm()`.

        The body points the user at the BEGIN/END marker block written by
        the client layer and asks them to paste it into a new GitHub issue.
        The block is PII-safe; the user can copy-paste verbatim.
        """
        body = _OPERATION_FAILED_NOTIFICATION_TEMPLATE.format(
            operation=operation,
            message=message,
            op_upper=marker_operation,
        )
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "message": body,
                "title": f"Verisure Italy — {operation} Failed",
                "notification_id": f"{DOMAIN}.operation_failed",
            },
        )

    def _fire_arming_exception_event(
        self, exc: ArmingExceptionError, mode: str
    ) -> None:
        """Fire an HA event for automation consumption."""
        self.hass.bus.async_fire(
            f"{DOMAIN}_arming_exception",
            {
                "entity_id": self.entity_id,
                "zones": [e.alias for e in exc.exceptions],
                "mode": mode,
            },
        )
