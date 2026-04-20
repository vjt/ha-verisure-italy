"""Probe module tests.

Assert schema, redaction, and happy-path parsing. These tests are the
backstop against probe output leaking PII — if any sensitive field
appears in the redaction assertion, it's a release-blocking bug.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from aiohttp import ClientSession
from aioresponses import aioresponses

from verisure_italy.client import API_URL, VerisureClient
from verisure_italy.models import Installation
from verisure_italy.probe import (
    _PII_FIELDS,
    PROBE_SCHEMA_VERSION,
    _hash_numinst,
    assert_redacted,
    run_probe,
)

INSTALLATION = Installation(
    number="1234567",
    alias="Casa",
    panel="SDVECU",
    type="VERISURE",
    name="Mario",
    surname="Rossi",
    address="Via Test 1",
    city="Roma",
    postcode="00100",
    province="RM",
    email="m@r.it",
    phone="+390000000",
)


def _make_jwt() -> str:
    exp = (datetime.now(tz=UTC) + timedelta(minutes=60)).timestamp()
    return pyjwt.encode(
        {"exp": exp},
        "test-secret-key-long-enough-for-hs256",
        algorithm="HS256",
    )


def _services_body() -> str:
    return json.dumps({
        "data": {
            "xSSrv": {
                "res": "OK",
                "msg": "",
                "installation": {
                    "numinst": INSTALLATION.number,
                    "capabilities": _make_jwt(),
                    "services": [
                        {
                            "idService": 11,
                            "active": True,
                            "visible": True,
                            "request": "EST",
                            "bde": True,
                            "isPremium": False,
                            "codOper": True,
                            "minWrapperVersion": "10.0.0",
                            "description": "Alarm Status",
                            "attributes": {
                                "attributes": [
                                    {"name": "MODE_ARM", "value": "ARM1", "active": True},
                                    {"name": "MODE_ARMPERI", "value": "ARM1PERI1", "active": True},
                                ],
                            },
                        },
                        {
                            "idService": 506,
                            "active": True,
                            "visible": True,
                            "request": "TIMELINE",
                            "bde": False,
                            "description": "Timeline",
                            "attributes": None,
                        },
                    ],
                },
            }
        }
    })


def _device_list_body() -> str:
    return json.dumps({
        "data": {
            "xSDeviceList": {
                "res": "OK",
                "devices": [
                    {
                        "id": "0",
                        "code": "1",
                        "zoneId": None,
                        "name": "Centrale",
                        "type": "CENT",
                        "isActive": None,
                        "serialNumber": "SECRET-SERIAL-001",
                    },
                    {
                        "id": "12",
                        "code": "12",
                        "zoneId": "QR12",
                        "name": "Cucina",
                        "type": "QR",
                        "isActive": True,
                        "serialNumber": "SECRET-SERIAL-002",
                    },
                ],
            }
        }
    })


def _status_body() -> str:
    return json.dumps({
        "data": {
            "xSStatus": {
                "status": "0",
                "timestampUpdate": "2026-04-20T12:00:00",
                "exceptions": None,
            }
        }
    })


@pytest.fixture
def mock_api():
    with aioresponses() as m:
        yield m


@pytest.fixture
async def http_session():
    async with ClientSession() as session:
        yield session


@pytest.fixture
async def client(http_session):
    c = VerisureClient(
        username="u@e.it",
        password="pw",
        http_session=http_session,
        device_id="dev-id",
        uuid="uuid-x",
        id_device_indigitall="indigi",
        poll_delay=0.0,
        poll_timeout=2.0,
    )
    # pre-auth so _ensure_auth is a no-op
    c._auth_token = "tok"
    c._auth_token_exp = datetime.now(tz=UTC) + timedelta(minutes=30)
    c._capabilities[INSTALLATION.number] = _make_jwt()
    c._capabilities_exp[INSTALLATION.number] = (
        datetime.now(tz=UTC) + timedelta(minutes=30)
    )
    return c


class TestHashNuminst:
    def test_stable(self):
        assert _hash_numinst("1234567") == _hash_numinst("1234567")

    def test_matches_sha256_prefix(self):
        expected = hashlib.sha256(b"1234567").hexdigest()[:8]
        assert _hash_numinst("1234567") == expected

    def test_different_inputs_differ(self):
        assert _hash_numinst("111") != _hash_numinst("222")


class TestRunProbe:
    async def test_schema_version(self, mock_api, client):
        mock_api.post(API_URL, body=_services_body())
        mock_api.post(API_URL, body=_device_list_body())
        mock_api.post(API_URL, body=_status_body())

        probe = await run_probe(client, INSTALLATION)

        assert probe["schema_version"] == PROBE_SCHEMA_VERSION

    async def test_contains_expected_top_level_keys(self, mock_api, client):
        mock_api.post(API_URL, body=_services_body())
        mock_api.post(API_URL, body=_device_list_body())
        mock_api.post(API_URL, body=_status_body())

        probe = await run_probe(client, INSTALLATION)

        assert set(probe.keys()) >= {
            "schema_version", "timestamp", "client_version",
            "installation", "services", "devices", "alarm_state",
        }

    async def test_installation_hash_replaces_numinst(self, mock_api, client):
        mock_api.post(API_URL, body=_services_body())
        mock_api.post(API_URL, body=_device_list_body())
        mock_api.post(API_URL, body=_status_body())

        probe = await run_probe(client, INSTALLATION)

        assert probe["installation"]["panel"] == "SDVECU"
        assert probe["installation"]["type"] == "VERISURE"
        assert probe["installation"]["numinst_hash"] == _hash_numinst("1234567")
        assert "numinst" not in probe["installation"]
        assert "alias" not in probe["installation"]

    async def test_services_attributes_preserved(self, mock_api, client):
        mock_api.post(API_URL, body=_services_body())
        mock_api.post(API_URL, body=_device_list_body())
        mock_api.post(API_URL, body=_status_body())

        probe = await run_probe(client, INSTALLATION)

        est = next(s for s in probe["services"] if s["request"] == "EST")
        attrs = est["attributes"]
        assert len(attrs) == 2
        assert {"name": "MODE_ARM", "value": "ARM1", "active": True} in attrs

    async def test_services_empty_attributes(self, mock_api, client):
        mock_api.post(API_URL, body=_services_body())
        mock_api.post(API_URL, body=_device_list_body())
        mock_api.post(API_URL, body=_status_body())

        probe = await run_probe(client, INSTALLATION)

        timeline = next(s for s in probe["services"] if s["request"] == "TIMELINE")
        assert timeline["attributes"] == []

    async def test_devices_redacted(self, mock_api, client):
        mock_api.post(API_URL, body=_services_body())
        mock_api.post(API_URL, body=_device_list_body())
        mock_api.post(API_URL, body=_status_body())

        probe = await run_probe(client, INSTALLATION)

        assert len(probe["devices"]) == 2
        for dev in probe["devices"]:
            assert "name" not in dev
            assert "serialNumber" not in dev
            assert "serial_number" not in dev
        types = {d["type"] for d in probe["devices"]}
        assert types == {"CENT", "QR"}

    async def test_redaction_completeness(self, mock_api, client):
        """No PII field present anywhere in the probe output."""
        mock_api.post(API_URL, body=_services_body())
        mock_api.post(API_URL, body=_device_list_body())
        mock_api.post(API_URL, body=_status_body())

        probe = await run_probe(client, INSTALLATION)

        # Should not raise — belt-and-braces check.
        assert_redacted(probe)

    async def test_no_pii_values_in_serialized_output(self, mock_api, client):
        """Serialize probe and ensure sensitive string values don't appear."""
        mock_api.post(API_URL, body=_services_body())
        mock_api.post(API_URL, body=_device_list_body())
        mock_api.post(API_URL, body=_status_body())

        probe = await run_probe(client, INSTALLATION)
        text = json.dumps(probe)

        forbidden_values = [
            INSTALLATION.number, "Mario", "Rossi", "Via Test 1", "Roma",
            "m@r.it", "+390000000", "Casa", "SECRET-SERIAL-001",
            "SECRET-SERIAL-002", "Centrale", "Cucina",
        ]
        for val in forbidden_values:
            assert val not in text, f"PII value leaked: {val!r}"


class TestAssertRedacted:
    def test_accepts_clean_dict(self):
        assert_redacted({"panel": "SDVECU", "services": [{"idService": 11}]})

    def test_raises_on_top_level_pii(self):
        with pytest.raises(ValueError, match="numinst"):
            assert_redacted({"numinst": "1234567"})

    def test_raises_on_nested_pii(self):
        with pytest.raises(ValueError, match="phone"):
            assert_redacted({"installation": {"panel": "X", "phone": "+1"}})

    def test_raises_on_list_item_pii(self):
        with pytest.raises(ValueError, match="serialNumber"):
            assert_redacted({"devices": [{"id": "0", "serialNumber": "X"}]})


class TestPIIFieldsSet:
    def test_covers_core_identifiers(self):
        required = {"numinst", "phone", "email", "address",
                    "serialNumber", "capabilities", "referenceId"}
        missing = required - _PII_FIELDS
        assert not missing, f"PII set missing: {missing}"


class TestSDVECUReferenceFixture:
    """Regression: committed SDVECU probe parses, matches schema v1, redacted."""

    from pathlib import Path as _Path
    FIXTURE_PATH = (
        _Path(__file__).parent / "fixtures" / "probe_sdvecu_reference.json"
    )

    def _load(self) -> dict[str, object]:
        with self.FIXTURE_PATH.open() as f:
            return json.load(f)  # type: ignore[no-any-return]

    def test_fixture_exists(self):
        assert self.FIXTURE_PATH.exists()

    def test_schema_version(self):
        assert self._load()["schema_version"] == PROBE_SCHEMA_VERSION

    def test_top_level_keys(self):
        keys = set(self._load().keys())
        assert keys >= {
            "schema_version", "timestamp", "client_version",
            "installation", "services", "devices", "alarm_state",
        }

    def test_panel_is_sdvecu(self):
        inst = self._load()["installation"]
        assert isinstance(inst, dict)
        assert inst["panel"] == "SDVECU"

    def test_numinst_hash_is_placeholder(self):
        """Fixture must not carry a real panel identifier."""
        inst = self._load()["installation"]
        assert isinstance(inst, dict)
        assert inst["numinst_hash"] == "REDACTED"

    def test_arm_service_present(self):
        services = self._load()["services"]
        assert isinstance(services, list)
        arm_services = [
            s for s in services if isinstance(s, dict) and s.get("request") == "ARM"
        ]
        assert len(arm_services) == 1, "SDVECU has one ARM service"

    def test_redaction(self):
        assert_redacted(self._load())

    def test_no_leaked_jwts(self):
        text = json.dumps(self._load())
        assert "eyJ" not in text, "JWT prefix leaked in fixture"
