"""Client integration tests with mocked HTTP responses.

Tests the VerisureClient against realistic API responses using aioresponses.
Mocks at the HTTP boundary — everything inside is real code: parsing, error
detection, polling, state management.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import jwt as pyjwt
import pytest
from aiohttp import ClientConnectorError, ClientSession
from aioresponses import aioresponses

from verisure_api.client import API_URL, VerisureClient
from verisure_api.exceptions import (
    APIConnectionError,
    APIResponseError,
    AuthenticationError,
    OperationFailedError,
    OperationTimeoutError,
    SessionExpiredError,
    TwoFactorRequiredError,
    WAFBlockedError,
)
from verisure_api.models import (
    AlarmState,
    Installation,
    InteriorMode,
    PerimeterMode,
    ProtoCode,
)

# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------


def _make_jwt(exp_minutes: int = 60) -> str:
    """Create a JWT with an exp claim."""
    exp = (datetime.now() + timedelta(minutes=exp_minutes)).timestamp()
    return pyjwt.encode(
        {"exp": exp},
        "test-secret-key-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )


INSTALLATION = Installation(
    number="1234567",
    alias="Casa",
    panel="SDVECU",
    type="VERISURE",
    name="Test",
    surname="User",
    address="Via Test 1",
    city="Roma",
    postcode="00100",
    province="RM",
    email="test@test.it",
    phone="+39000000000",
)


@pytest.fixture
def mock_api():
    with aioresponses() as m:
        yield m


@pytest.fixture
async def http_session():
    async with ClientSession() as session:
        yield session


@pytest.fixture
def client(http_session: ClientSession) -> VerisureClient:
    return VerisureClient(
        username="test@test.it",
        password="password123",
        http_session=http_session,
        device_id="test-device-id",
        uuid="test-uuid-1234",
        id_device_indigitall="test-indigitall",
        poll_delay=0.0,
        poll_timeout=2.0,
    )


def _authenticate(client: VerisureClient) -> None:
    """Pre-populate auth state so operation tests skip the login flow."""
    token = _make_jwt(60)
    client._auth_token = token
    client._auth_token_exp = datetime.now() + timedelta(hours=1)
    client._login_timestamp = int(datetime.now().timestamp() * 1000)

    cap_token = _make_jwt(60)
    client._capabilities[INSTALLATION.number] = cap_token
    client._capabilities_exp[INSTALLATION.number] = datetime.now() + timedelta(hours=1)


# ---------------------------------------------------------------------------
# JSON response builders — one per API operation
# ---------------------------------------------------------------------------


def _login_ok(token: str | None = None) -> str:
    token = token or _make_jwt()
    return json.dumps({
        "data": {
            "xSLoginToken": {
                "res": "OK",
                "msg": "Login successful",
                "hash": token,
                "refreshToken": "refresh-abc",
                "needDeviceAuthorization": False,
            }
        }
    })


def _login_2fa_required() -> str:
    return json.dumps({
        "data": {
            "xSLoginToken": {
                "res": "OK",
                "msg": "",
                "hash": None,
                "refreshToken": None,
                "needDeviceAuthorization": True,
            }
        }
    })


def _login_null_hash() -> str:
    return json.dumps({
        "data": {
            "xSLoginToken": {
                "res": "OK",
                "msg": "",
                "hash": None,
                "refreshToken": None,
                "needDeviceAuthorization": False,
            }
        }
    })


def _installation_list() -> str:
    return json.dumps({
        "data": {
            "xSInstallations": {
                "installations": [
                    {
                        "number": INSTALLATION.number,
                        "alias": INSTALLATION.alias,
                        "panel": INSTALLATION.panel,
                        "type": INSTALLATION.type,
                        "name": INSTALLATION.name,
                        "surname": INSTALLATION.surname,
                        "address": INSTALLATION.address,
                        "city": INSTALLATION.city,
                        "postcode": INSTALLATION.postcode,
                        "province": INSTALLATION.province,
                        "email": INSTALLATION.email,
                        "phone": INSTALLATION.phone,
                    }
                ]
            }
        }
    })


def _services_response(capabilities_token: str | None = None) -> str:
    cap = capabilities_token or _make_jwt()
    return json.dumps({
        "data": {
            "xSSrv": {
                "res": "OK",
                "msg": "",
                "installation": {
                    "numinst": INSTALLATION.number,
                    "capabilities": cap,
                    "services": [
                        {
                            "idService": 11,
                            "active": True,
                            "visible": True,
                            "request": "EST",
                            "description": "Alarm Status",
                        },
                        {
                            "idService": 506,
                            "active": True,
                            "visible": True,
                            "request": "TIMELINE",
                            "description": "Timeline",
                        },
                    ],
                },
            }
        }
    })


def _check_alarm_response(reference_id: str = "ref-123") -> str:
    return json.dumps({
        "data": {
            "xSCheckAlarm": {
                "res": "OK",
                "msg": "",
                "referenceId": reference_id,
            }
        }
    })


def _alarm_status_pending() -> str:
    return json.dumps({
        "data": {
            "xSCheckAlarmStatus": {
                "res": "WAIT",
                "msg": "pending",
                "status": "0",
                "numinst": INSTALLATION.number,
                "protomResponse": "",
                "protomResponseDate": "",
            }
        }
    })


def _alarm_status_complete(proto_code: str = "A") -> str:
    msg_map: dict[str, str] = {
        "D": "inactive_alarm",
        "A": "active_perimeter_plus_alarm",
        "B": "armed_partial_plus_perimeter",
        "P": "armed_partial",
        "E": "active_perimetral_alarm_msg",
        "T": "total_armed",
    }
    return json.dumps({
        "data": {
            "xSCheckAlarmStatus": {
                "res": "OK",
                "msg": msg_map[proto_code],
                "status": "0",
                "numinst": INSTALLATION.number,
                "protomResponse": proto_code,
                "protomResponseDate": "2026-04-02T10:30:00",
            }
        }
    })


def _general_status(status: str = "A") -> str:
    return json.dumps({
        "data": {
            "xSStatus": {
                "status": status,
                "timestampUpdate": "2026-04-02T10:30:00",
            }
        }
    })


def _arm_panel_response(reference_id: str = "arm-ref-123") -> str:
    return json.dumps({
        "data": {
            "xSArmPanel": {
                "res": "OK",
                "msg": "",
                "referenceId": reference_id,
            }
        }
    })


def _arm_panel_rejected() -> str:
    return json.dumps({
        "data": {
            "xSArmPanel": {
                "res": "ERROR",
                "msg": "Panel busy",
                "referenceId": "",
            }
        }
    })


def _arm_status_pending() -> str:
    return json.dumps({
        "data": {
            "xSArmStatus": {
                "res": "WAIT",
                "msg": "pending",
                "status": "0",
                "numinst": INSTALLATION.number,
                "protomResponse": "",
                "protomResponseDate": "",
                "requestId": "",
                "error": None,
            }
        }
    })


def _arm_status_complete(proto_code: str = "A") -> str:
    return json.dumps({
        "data": {
            "xSArmStatus": {
                "res": "OK",
                "msg": "armed",
                "status": "0",
                "numinst": INSTALLATION.number,
                "protomResponse": proto_code,
                "protomResponseDate": "2026-04-02T10:30:00",
                "requestId": "req-123",
                "error": None,
            }
        }
    })


def _disarm_panel_response(reference_id: str = "disarm-ref-123") -> str:
    return json.dumps({
        "data": {
            "xSDisarmPanel": {
                "res": "OK",
                "msg": "",
                "referenceId": reference_id,
            }
        }
    })


def _disarm_panel_rejected() -> str:
    return json.dumps({
        "data": {
            "xSDisarmPanel": {
                "res": "ERROR",
                "msg": "Panel not responding",
                "referenceId": "",
            }
        }
    })


def _disarm_status_pending() -> str:
    return json.dumps({
        "data": {
            "xSDisarmStatus": {
                "res": "WAIT",
                "msg": "pending",
                "numinst": INSTALLATION.number,
                "protomResponse": "",
                "protomResponseDate": "",
                "requestId": "",
                "error": None,
            }
        }
    })


def _disarm_status_complete() -> str:
    return json.dumps({
        "data": {
            "xSDisarmStatus": {
                "res": "OK",
                "msg": "inactive_alarm",
                "numinst": INSTALLATION.number,
                "protomResponse": "D",
                "protomResponseDate": "2026-04-02T10:31:00",
                "requestId": "req-456",
                "error": None,
            }
        }
    })


def _send_otp_ok() -> str:
    return json.dumps({
        "data": {
            "xSSendOtp": {
                "res": "OK",
                "msg": "OTP sent",
            }
        }
    })


def _error_session_expired() -> str:
    return json.dumps({
        "errors": [
            {
                "message": "Session expired",
                "data": {"status": 403},
            }
        ]
    })


def _error_2fa_required() -> str:
    return json.dumps({
        "errors": [
            {
                "message": "Need device authorization",
                "data": {"needDeviceAuthorization": True},
            }
        ]
    })


def _error_generic(message: str = "Something went wrong") -> str:
    return json.dumps({
        "errors": [
            {"message": message},
        ]
    })


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------


class TestLogin:
    async def test_success(self, mock_api, client):
        token = _make_jwt()
        mock_api.post(API_URL, body=_login_ok(token))

        result = await client.login()

        assert result.res == "OK"
        assert result.hash == token
        assert client._auth_token == token

    async def test_stores_refresh_token(self, mock_api, client):
        mock_api.post(API_URL, body=_login_ok())

        await client.login()

        assert client._refresh_token == "refresh-abc"

    async def test_sets_token_expiry(self, mock_api, client):
        token = _make_jwt(30)
        mock_api.post(API_URL, body=_login_ok(token))

        await client.login()

        assert client._auth_token_exp > datetime.now()
        assert client._auth_token_exp < datetime.now() + timedelta(minutes=31)

    async def test_2fa_required(self, mock_api, client):
        mock_api.post(API_URL, body=_login_2fa_required())

        with pytest.raises(TwoFactorRequiredError):
            await client.login()

    async def test_null_hash_raises(self, mock_api, client):
        mock_api.post(API_URL, body=_login_null_hash())

        with pytest.raises(AuthenticationError, match="null auth token"):
            await client.login()

    async def test_graphql_error_raises_auth_error(self, mock_api, client):
        mock_api.post(API_URL, body=_error_generic("Invalid credentials"))

        with pytest.raises(AuthenticationError, match="Login failed"):
            await client.login()


class TestLogout:
    async def test_clears_auth_state(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, body='{"data": {"xSLogout": {"res": "OK"}}}')

        await client.logout()

        assert client._auth_token is None
        assert client._auth_token_exp == datetime.min
        assert client._login_timestamp == 0
        assert client._refresh_token == ""

    async def test_clears_state_even_on_error(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, status=500, body="Internal Server Error")

        with pytest.raises(APIResponseError):
            await client.logout()

        assert client._auth_token is None


class TestSendOtp:
    async def test_send_otp_success(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, body=_send_otp_ok())

        result = await client.send_otp(phone_id=1, otp_hash="abc123")

        assert result is True


# ---------------------------------------------------------------------------
# Installation & services tests
# ---------------------------------------------------------------------------


class TestListInstallations:
    async def test_returns_installations(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, body=_installation_list())

        installations = await client.list_installations()

        assert len(installations) == 1
        inst = installations[0]
        assert inst.number == "1234567"
        assert inst.panel == "SDVECU"
        assert inst.alias == "Casa"


class TestGetServices:
    async def test_returns_services(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, body=_services_response())

        services = await client.get_services(INSTALLATION)

        assert len(services) == 2
        assert services[0].request == "EST"
        assert services[0].id_service == 11
        assert services[1].request == "TIMELINE"
        assert services[1].id_service == 506

    async def test_stores_capabilities_token(self, mock_api, client):
        _authenticate(client)
        cap_token = _make_jwt(120)
        mock_api.post(API_URL, body=_services_response(cap_token))

        await client.get_services(INSTALLATION)

        assert client._capabilities[INSTALLATION.number] == cap_token
        assert INSTALLATION.number in client._capabilities_exp
        assert client._capabilities_exp[INSTALLATION.number] > datetime.now()


# ---------------------------------------------------------------------------
# Alarm status tests
# ---------------------------------------------------------------------------


class TestCheckAlarm:
    async def test_returns_reference_id(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, body=_check_alarm_response("ref-abc"))

        ref_id = await client.check_alarm(INSTALLATION)

        assert ref_id == "ref-abc"


class TestPollAlarmStatus:
    async def test_complete_immediately(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, body=_alarm_status_complete("A"))

        result = await client.poll_alarm_status(INSTALLATION, "ref-123")

        assert result.protom_response == "A"
        assert result.alarm_state == AlarmState(
            interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON
        )
        assert not result.is_pending

    async def test_pending_then_complete(self, mock_api, client):
        _authenticate(client)
        # First poll: WAIT, second poll: complete
        mock_api.post(API_URL, body=_alarm_status_pending())
        mock_api.post(API_URL, body=_alarm_status_complete("D"))

        result = await client.poll_alarm_status(INSTALLATION, "ref-123")

        assert result.protom_response == "D"
        assert result.proto_code == ProtoCode.DISARMED

    async def test_multiple_pending_then_complete(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, body=_alarm_status_pending())
        mock_api.post(API_URL, body=_alarm_status_pending())
        mock_api.post(API_URL, body=_alarm_status_pending())
        mock_api.post(API_URL, body=_alarm_status_complete("B"))

        result = await client.poll_alarm_status(INSTALLATION, "ref-123")

        assert result.proto_code == ProtoCode.PARTIAL_PERIMETER

    async def test_timeout(self, mock_api, client):
        _authenticate(client)
        client._poll_timeout = 0.1  # Short timeout for fast test
        mock_api.post(API_URL, body=_alarm_status_pending(), repeat=True)

        with pytest.raises(OperationTimeoutError, match="did not complete"):
            await client.poll_alarm_status(INSTALLATION, "ref-123")

    async def test_all_proto_codes(self, mock_api, client):
        """Every valid proto code resolves to the correct alarm state."""
        expected: dict[str, AlarmState] = {
            "D": AlarmState(interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF),
            "E": AlarmState(interior=InteriorMode.OFF, perimeter=PerimeterMode.ON),
            "P": AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.OFF),
            "B": AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON),
            "T": AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.OFF),
            "A": AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON),
        }

        for code, state in expected.items():
            _authenticate(client)
            mock_api.post(API_URL, body=_alarm_status_complete(code))

            result = await client.poll_alarm_status(INSTALLATION, f"ref-{code}")

            assert result.alarm_state == state, f"Proto code {code} → wrong state"


class TestGetGeneralStatus:
    async def test_returns_status(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, body=_general_status("B"))

        status = await client.get_general_status(INSTALLATION)

        assert status.status == "B"
        assert status.timestamp_update == "2026-04-02T10:30:00"


# ---------------------------------------------------------------------------
# Arm / disarm tests
# ---------------------------------------------------------------------------


class TestArm:
    async def test_total_perimeter(self, mock_api, client):
        _authenticate(client)
        target = AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON)

        mock_api.post(API_URL, body=_arm_panel_response("arm-ref-1"))
        mock_api.post(API_URL, body=_arm_status_complete("A"))

        result = await client.arm(INSTALLATION, target)

        assert result.proto_code == ProtoCode.TOTAL_PERIMETER
        assert result.alarm_state == target
        assert client._last_proto == "A"

    async def test_partial_perimeter(self, mock_api, client):
        _authenticate(client)
        target = AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON)

        mock_api.post(API_URL, body=_arm_panel_response())
        mock_api.post(API_URL, body=_arm_status_complete("B"))

        result = await client.arm(INSTALLATION, target)

        assert result.proto_code == ProtoCode.PARTIAL_PERIMETER

    async def test_with_pending_polls(self, mock_api, client):
        _authenticate(client)
        target = AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON)

        mock_api.post(API_URL, body=_arm_panel_response())
        mock_api.post(API_URL, body=_arm_status_pending())
        mock_api.post(API_URL, body=_arm_status_pending())
        mock_api.post(API_URL, body=_arm_status_complete("A"))

        result = await client.arm(INSTALLATION, target)

        assert result.protom_response == "A"

    async def test_rejected_by_panel(self, mock_api, client):
        _authenticate(client)
        target = AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON)

        mock_api.post(API_URL, body=_arm_panel_rejected())

        with pytest.raises(OperationFailedError, match="Panel busy"):
            await client.arm(INSTALLATION, target)

    async def test_updates_last_proto(self, mock_api, client):
        _authenticate(client)
        assert client._last_proto == ""

        target = AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON)
        mock_api.post(API_URL, body=_arm_panel_response())
        mock_api.post(API_URL, body=_arm_status_complete("B"))

        await client.arm(INSTALLATION, target)

        assert client._last_proto == "B"


class TestDisarm:
    async def test_success(self, mock_api, client):
        _authenticate(client)

        mock_api.post(API_URL, body=_disarm_panel_response())
        mock_api.post(API_URL, body=_disarm_status_complete())

        result = await client.disarm(INSTALLATION)

        assert result.proto_code == ProtoCode.DISARMED
        assert result.alarm_state.interior == InteriorMode.OFF
        assert result.alarm_state.perimeter == PerimeterMode.OFF
        assert client._last_proto == "D"

    async def test_with_pending_polls(self, mock_api, client):
        _authenticate(client)

        mock_api.post(API_URL, body=_disarm_panel_response())
        mock_api.post(API_URL, body=_disarm_status_pending())
        mock_api.post(API_URL, body=_disarm_status_complete())

        result = await client.disarm(INSTALLATION)

        assert result.protom_response == "D"

    async def test_rejected_by_panel(self, mock_api, client):
        _authenticate(client)

        mock_api.post(API_URL, body=_disarm_panel_rejected())

        with pytest.raises(OperationFailedError, match="Panel not responding"):
            await client.disarm(INSTALLATION)

    async def test_panel_error_during_poll(self, mock_api, client):
        """Panel accepts request but returns ERROR during poll (e.g. no permission)."""
        _authenticate(client)

        mock_api.post(API_URL, body=_disarm_panel_response())
        mock_api.post(API_URL, body=json.dumps({
            "data": {
                "xSDisarmStatus": {
                    "res": "ERROR",
                    "msg": "alarm-manager.error_no_response_to_request",
                    "numinst": None,
                    "protomResponse": "",
                    "protomResponseDate": "2026-04-02T20:30:59Z",
                    "requestId": "",
                    "error": None,
                }
            }
        }))

        with pytest.raises(OperationFailedError, match="Panel rejected operation"):
            await client.disarm(INSTALLATION)

    async def test_updates_last_proto(self, mock_api, client):
        _authenticate(client)
        client._last_proto = "A"  # Was armed

        mock_api.post(API_URL, body=_disarm_panel_response())
        mock_api.post(API_URL, body=_disarm_status_complete())

        await client.disarm(INSTALLATION)

        assert client._last_proto == "D"


# ---------------------------------------------------------------------------
# HTTP error handling
# ---------------------------------------------------------------------------


class TestHTTPErrors:
    async def test_waf_blocked_403(self, mock_api, client):
        _authenticate(client)
        mock_api.post(
            API_URL, status=403, body="_Incapsula_Resource blocked by WAF"
        )

        with pytest.raises(WAFBlockedError, match="Incapsula"):
            await client.list_installations()

    async def test_plain_403(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, status=403, body="Forbidden")

        with pytest.raises(APIResponseError, match="HTTP 403"):
            await client.list_installations()

    async def test_http_500(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, status=500, body="Internal Server Error")

        with pytest.raises(APIResponseError, match="HTTP 500"):
            await client.list_installations()

    async def test_http_429(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, status=429, body="Too Many Requests")

        with pytest.raises(APIResponseError, match="HTTP 429"):
            await client.list_installations()

    async def test_connection_error(self, mock_api, client):
        _authenticate(client)
        mock_api.post(
            API_URL,
            exception=ClientConnectorError(
                connection_key=MagicMock(),
                os_error=OSError("Connection refused"),
            ),
        )

        with pytest.raises(APIConnectionError, match="Connection error"):
            await client.list_installations()


# ---------------------------------------------------------------------------
# GraphQL error handling
# ---------------------------------------------------------------------------


class TestGraphQLErrors:
    async def test_session_expired(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, body=_error_session_expired())

        with pytest.raises(SessionExpiredError, match="Session expired"):
            await client.list_installations()

    async def test_2fa_required(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, body=_error_2fa_required())

        with pytest.raises(TwoFactorRequiredError):
            await client.list_installations()

    async def test_generic_error_message(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, body=_error_generic("Database timeout"))

        with pytest.raises(APIResponseError, match="Database timeout"):
            await client.list_installations()


# ---------------------------------------------------------------------------
# Auto-auth & token refresh
# ---------------------------------------------------------------------------


class TestEnsureAuth:
    async def test_auto_login_when_no_token(self, mock_api, client):
        """Client with no token auto-logs-in before operations."""
        token = _make_jwt()
        # _ensure_auth → login() → get_services() → actual operation
        mock_api.post(API_URL, body=_login_ok(token))
        mock_api.post(API_URL, body=_services_response())
        mock_api.post(API_URL, body=_general_status("D"))

        status = await client.get_general_status(INSTALLATION)

        assert status.status == "D"
        assert client._auth_token == token

    async def test_auto_login_when_token_expired(self, mock_api, client):
        """Client with expired token re-authenticates automatically."""
        client._auth_token = "expired-token"
        client._auth_token_exp = datetime.min  # Already expired

        new_token = _make_jwt()
        mock_api.post(API_URL, body=_login_ok(new_token))
        mock_api.post(API_URL, body=_services_response())
        mock_api.post(API_URL, body=_general_status("A"))

        status = await client.get_general_status(INSTALLATION)

        assert status.status == "A"
        assert client._auth_token == new_token

    async def test_auto_refresh_capabilities(self, mock_api, client):
        """Expired capabilities token triggers get_services refresh."""
        token = _make_jwt()
        client._auth_token = token
        client._auth_token_exp = datetime.now() + timedelta(hours=1)
        client._login_timestamp = int(datetime.now().timestamp() * 1000)
        # Capabilities expired
        client._capabilities_exp[INSTALLATION.number] = datetime.min

        cap_token = _make_jwt()
        mock_api.post(API_URL, body=_services_response(cap_token))
        mock_api.post(API_URL, body=_general_status("B"))

        status = await client.get_general_status(INSTALLATION)

        assert status.status == "B"
        assert client._capabilities[INSTALLATION.number] == cap_token
