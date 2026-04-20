"""Panel-type gate in alarm_control_panel.

Covers the fail-secure behaviour: unknown panel types must raise
UnsupportedPanelError, emit a probe to the log, and send zero bytes
to the Verisure API. SDVECU (supported) must pass through untouched.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from verisure_italy.exceptions import UnsupportedPanelError
from verisure_italy.models import SUPPORTED_PANELS, Installation


def _make_installation(panel: str) -> Installation:
    return Installation(
        number="1234567", alias="Home", panel=panel, type="home",
    )


def _make_panel_entity(panel: str):
    """Build a VerisureAlarmPanel with just enough structure for the gate."""
    from custom_components.verisure_italy.alarm_control_panel import (
        VerisureAlarmPanel,
    )

    coordinator = MagicMock()
    coordinator.installation = _make_installation(panel)
    coordinator.client = MagicMock()
    coordinator.data = MagicMock()
    coordinator.data.alarm_state = MagicMock()
    coordinator.force_context = None

    # VerisureAlarmPanel.__init__ reads coordinator.data.alarm_state through
    # _STATE_MAP — bypass __init__ to skip HA entity wiring we don't need here.
    entity = VerisureAlarmPanel.__new__(VerisureAlarmPanel)
    entity.coordinator = coordinator  # type: ignore[attr-defined]
    entity.hass = MagicMock()  # type: ignore[attr-defined]
    entity.hass.services.async_call = AsyncMock()
    return entity, coordinator


class TestSupportedPanelList:
    def test_sdvecu_is_supported(self):
        assert "SDVECU" in SUPPORTED_PANELS

    def test_cent_is_not_supported(self):
        assert "CENT" not in SUPPORTED_PANELS


class TestCheckPanelSupported:
    async def test_sdvecu_passes_through(self):
        entity, _ = _make_panel_entity("SDVECU")
        assert await entity._check_panel_supported("arm") is True

    async def test_cent_raises(self, caplog, monkeypatch):
        entity, coordinator = _make_panel_entity("CENT")
        fake_probe = {"schema_version": 1, "installation": {"panel": "CENT"}}
        monkeypatch.setattr(
            "custom_components.verisure_italy.alarm_control_panel.run_probe",
            AsyncMock(return_value=fake_probe),
        )
        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ), pytest.raises(UnsupportedPanelError) as exc:
            await entity._check_panel_supported("arm")

        assert exc.value.panel == "CENT"
        # Probe markers must appear so users can locate the dump in logs
        combined = "\n".join(r.message for r in caplog.records)
        assert "VERISURE PROBE BEGIN" in combined
        assert "VERISURE PROBE END" in combined
        assert "CENT" in combined
        # Client-level arm/disarm must NOT have been invoked
        coordinator.async_arm.assert_not_called()
        coordinator.async_disarm.assert_not_called()
        # User notification created
        entity.hass.services.async_call.assert_awaited()

    async def test_probe_failure_does_not_mask_error(self, caplog, monkeypatch):
        """Even if probe throws, the gate must still refuse and raise."""
        entity, _ = _make_panel_entity("CENT")
        monkeypatch.setattr(
            "custom_components.verisure_italy.alarm_control_panel.run_probe",
            AsyncMock(side_effect=RuntimeError("network hiccup")),
        )
        with caplog.at_level(
            logging.ERROR,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ), pytest.raises(UnsupportedPanelError):
            await entity._check_panel_supported("arm")

        assert any("Probe failed" in r.message for r in caplog.records)

    async def test_probe_output_is_json_roundtrip(self, caplog, monkeypatch):
        """Probe block in log must be valid JSON (preserves reporter workflow)."""
        entity, _ = _make_panel_entity("ACME9000")
        fake_probe = {
            "schema_version": 1,
            "installation": {"panel": "ACME9000", "type": "home"},
            "services": [{"idService": 31, "request": "ARM"}],
            "devices": [],
            "alarm_state": {"status": "D", "exceptions": []},
        }
        monkeypatch.setattr(
            "custom_components.verisure_italy.alarm_control_panel.run_probe",
            AsyncMock(return_value=fake_probe),
        )
        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.verisure_italy.alarm_control_panel",
        ), pytest.raises(UnsupportedPanelError):
            await entity._check_panel_supported("arm")

        block = next(
            r for r in caplog.records
            if "VERISURE PROBE BEGIN" in r.message
        )
        # Extract JSON between markers
        raw = block.message
        begin = raw.index("VERISURE PROBE BEGIN") + len("VERISURE PROBE BEGIN")
        end = raw.index("=== VERISURE PROBE END ===")
        payload = raw[begin:end].strip()
        # First chars are "===\n" from the marker; strip until first `{`
        payload = payload[payload.index("{"):]
        parsed = json.loads(payload)
        assert parsed["installation"]["panel"] == "ACME9000"
