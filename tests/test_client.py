"""Client integration tests with mocked HTTP responses.

Tests the VerisureClient against realistic API responses using aioresponses.
Mocks at the HTTP boundary — everything inside is real code: parsing, error
detection, polling, state management.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import jwt as pyjwt
import pytest
from aiohttp import ClientConnectorError, ClientSession
from aioresponses import aioresponses

from verisure_italy.client import API_URL, VerisureClient
from verisure_italy.exceptions import (
    APIConnectionError,
    APIResponseError,
    ArmingExceptionError,
    AuthenticationError,
    OperationFailedError,
    OperationTimeoutError,
    SessionExpiredError,
    StateNotObservedError,
    TwoFactorRequiredError,
    UnexpectedStateError,
    WAFBlockedError,
)
from verisure_italy.models import (
    AlarmState,
    Installation,
    InteriorMode,
    PerimeterMode,
    ProtoCode,
    ServiceRequest,
)

# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------


def _make_jwt(exp_minutes: int = 60) -> str:
    """Create a JWT with an exp claim."""
    exp = (datetime.now(tz=UTC) + timedelta(minutes=exp_minutes)).timestamp()
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
def no_sleep(monkeypatch: pytest.MonkeyPatch):
    """Patch asyncio.sleep inside the client module to a no-op.

    Retries use real-time backoff (seconds). Tests must never block on
    wall-clock waits.
    """
    async def _fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("verisure_italy.client.asyncio.sleep", _fast_sleep)


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
    """Pre-populate auth state so operation tests skip the login flow.

    Also seeds the services cache with the live-observed SDVECU active
    services so arm/disarm tests don't need to register an xSSrv mock
    before the arm/disarm mutation.
    """
    token = _make_jwt(60)
    client._auth_token = token
    client._auth_token_exp = datetime.now(tz=UTC) + timedelta(hours=1)
    client._login_timestamp = int(datetime.now().timestamp() * 1000)

    cap_token = _make_jwt(60)
    client._capabilities[INSTALLATION.number] = cap_token
    client._capabilities_exp[INSTALLATION.number] = datetime.now(tz=UTC) + timedelta(hours=1)

    # SDVECU active services, live-verified on panel 1234567.
    client._services_cache[INSTALLATION.number] = frozenset({
        ServiceRequest.ARM,
        ServiceRequest.DARM,
        ServiceRequest.ARMDAY,
        ServiceRequest.ARMNIGHT,
        ServiceRequest.PERI,
    })

    # Realistic current-state observation — the coordinator polls
    # xSStatus at startup before any arm/disarm is dispatched. Tests
    # that want to model a specific current state should override this
    # via client.set_last_proto("X") after _authenticate().
    client.set_last_proto("D")


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


def _arm_status_with_error(
    error_type: str = "NON_BLOCKING",
    allow_forcing: bool = True,
    reference_id: str = "error-ref-123",
    suid: str = "error-suid-456",
) -> str:
    return json.dumps({
        "data": {
            "xSArmStatus": {
                "res": "ERROR",
                "msg": "alarm-manager.exceptions",
                "status": None,
                "protomResponse": None,
                "protomResponseDate": None,
                "numinst": None,
                "requestId": None,
                "error": {
                    "code": "EXCEPTIONS",
                    "type": error_type,
                    "allowForcing": allow_forcing,
                    "exceptionsNumber": 1,
                    "referenceId": reference_id,
                    "suid": suid,
                },
            }
        }
    })


def _get_exceptions_response(
    exceptions: list[dict[str, str]] | None = None,
) -> str:
    if exceptions is None:
        exceptions = [
            {"status": "OPEN", "deviceType": "MAGNETIC", "alias": "finestracucina"}
        ]
    return json.dumps({
        "data": {
            "xSGetExceptions": {
                "res": "OK",
                "msg": None,
                "exceptions": exceptions,
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


def _find_call_with_operation(mock_api: aioresponses, op_name: str):
    """Return the most-recent aioresponses RequestCall whose GraphQL
    operationName matches op_name, or raise AssertionError.
    """
    matches: list = []
    for call_list in mock_api.requests.values():
        for call in call_list:
            body = call.kwargs.get("json")
            if not isinstance(body, dict):
                continue
            if body.get("operationName") == op_name:
                matches.append(call)
    if not matches:
        raise AssertionError(
            f"No aioresponses call found with operationName={op_name!r}"
        )
    return matches[-1]


def _has_call_with_operation(mock_api: aioresponses, op_name: str) -> bool:
    """True if any recorded aioresponses request has the given operationName."""
    for call_list in mock_api.requests.values():
        for call in call_list:
            body = call.kwargs.get("json")
            if isinstance(body, dict) and body.get("operationName") == op_name:
                return True
    return False


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

        assert client._auth_token_exp > datetime.now(tz=UTC)
        assert client._auth_token_exp < datetime.now(tz=UTC) + timedelta(minutes=31)

    async def test_2fa_required(self, mock_api, client):
        mock_api.post(API_URL, body=_login_2fa_required())

        with pytest.raises(TwoFactorRequiredError):
            await client.login()

    async def test_null_hash_raises(self, mock_api, client):
        mock_api.post(API_URL, body=_login_null_hash())

        with pytest.raises(AuthenticationError, match="null auth token"):
            await client.login()

    async def test_graphql_error_propagates_as_api_error(
        self, mock_api, client, no_sleep
    ):
        """Transient GraphQL errors during login must NOT be classified as
        AuthenticationError. That would trigger ConfigEntryAuthFailed and
        lock the integration out for hours on an upstream server bug
        (docs/findings/unavailable-flapping.md).
        """
        # Register 3 mocks — retry will consume all three
        for _ in range(3):
            mock_api.post(
                API_URL,
                body=_error_generic(
                    "Login failed: Cannot read properties of undefined (reading 'it')"
                ),
            )

        with pytest.raises(APIResponseError, match="Cannot read properties"):
            await client.login()

    async def test_transient_graphql_error_retried_then_succeeds(
        self, mock_api, client, no_sleep
    ):
        """A single transient GraphQL error should be retried and not leak
        to the caller if a subsequent attempt succeeds.
        """
        token = _make_jwt()
        mock_api.post(
            API_URL,
            body=_error_generic(
                "Login failed: Cannot read properties of undefined (reading 'it')"
            ),
        )
        mock_api.post(API_URL, body=_login_ok(token))

        result = await client.login()

        assert result.hash == token
        assert client._auth_token == token


class TestLogout:
    async def test_clears_auth_state(self, mock_api, client):
        _authenticate(client)
        mock_api.post(API_URL, body='{"data": {"xSLogout": {"res": "OK"}}}')

        await client.logout()

        assert client._auth_token is None
        assert client._auth_token_exp == datetime.min.replace(tzinfo=UTC)
        assert client._login_timestamp == 0
        assert client._refresh_token == ""

    async def test_clears_state_even_on_error(
        self, mock_api, client, no_sleep
    ):
        _authenticate(client)
        # 5xx is retried — register 3 mocks, one per attempt
        for _ in range(3):
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

    async def test_null_metadata_fields_accepted(self, mock_api, client):
        """Verisure returns ``null`` for metadata fields on some installations.

        Issue #2: ``name`` came back null and crashed setup. Pre-empt all
        non-load-bearing metadata fields (``name``, ``surname``, ``address``,
        ``city``, ``postcode``, ``province``, ``email``, ``phone``,
        ``type``) — only ``number``, ``panel``, ``alias`` are required.
        """
        _authenticate(client)
        body = json.dumps({
            "data": {
                "xSInstallations": {
                    "installations": [{
                        "number": "1234567",
                        "alias": "Casa",
                        "panel": "SDVECU",
                        "type": None,
                        "name": None,
                        "surname": None,
                        "address": None,
                        "city": None,
                        "postcode": None,
                        "province": None,
                        "email": None,
                        "phone": None,
                    }]
                }
            }
        })
        mock_api.post(API_URL, body=body)

        installations = await client.list_installations()

        assert len(installations) == 1
        inst = installations[0]
        assert inst.number == "1234567"
        assert inst.alias == "Casa"
        assert inst.name is None
        assert inst.surname is None
        assert inst.email is None


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
        assert client._capabilities_exp[INSTALLATION.number] > datetime.now(tz=UTC)


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
        # _authenticate seeds _last_proto="D" (disarmed baseline).
        assert client._last_proto == "D"

        target = AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON)
        mock_api.post(API_URL, body=_arm_panel_response())
        mock_api.post(API_URL, body=_arm_status_complete("B"))

        await client.arm(INSTALLATION, target)

        assert client._last_proto == "B"


class TestDisarm:
    async def test_success(self, mock_api, client):
        _authenticate(client)
        client.set_last_proto("A")  # Armed total+peri — a realistic disarm scenario.

        mock_api.post(API_URL, body=_disarm_panel_response())
        mock_api.post(API_URL, body=_disarm_status_complete())

        result = await client.disarm(INSTALLATION)

        assert result.proto_code == ProtoCode.DISARMED
        assert result.alarm_state.interior == InteriorMode.OFF
        assert result.alarm_state.perimeter == PerimeterMode.OFF
        assert client._last_proto == "D"

    async def test_with_pending_polls(self, mock_api, client):
        _authenticate(client)
        client.set_last_proto("A")  # Armed — must be non-disarmed to resolve command.

        mock_api.post(API_URL, body=_disarm_panel_response())
        mock_api.post(API_URL, body=_disarm_status_pending())
        mock_api.post(API_URL, body=_disarm_status_complete())

        result = await client.disarm(INSTALLATION)

        assert result.protom_response == "D"

    async def test_rejected_by_panel(self, mock_api, client):
        _authenticate(client)
        client.set_last_proto("A")  # Armed — resolver must resolve before HTTP call.

        mock_api.post(API_URL, body=_disarm_panel_rejected())

        with pytest.raises(OperationFailedError, match="Panel not responding"):
            await client.disarm(INSTALLATION)

    async def test_panel_error_during_poll(self, mock_api, client):
        """Panel accepts request but returns ERROR during poll (e.g. no permission)."""
        _authenticate(client)
        client.set_last_proto("A")  # Armed — resolver must resolve before HTTP call.

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

    async def test_http_500_exhausts_retries(self, mock_api, client, no_sleep):
        """5xx is transient — retried 3 times total, then surfaces."""
        _authenticate(client)
        for _ in range(3):
            mock_api.post(API_URL, status=500, body="Internal Server Error")

        with pytest.raises(APIResponseError, match="HTTP 500"):
            await client.list_installations()

    async def test_http_429_not_retried(self, mock_api, client):
        """4xx is a client-side error — not retried."""
        _authenticate(client)
        mock_api.post(API_URL, status=429, body="Too Many Requests")

        with pytest.raises(APIResponseError, match="HTTP 429"):
            await client.list_installations()

    async def test_connection_error_exhausts_retries(
        self, mock_api, client, no_sleep
    ):
        """Network-level failure is transient — retried 3 times total."""
        _authenticate(client)
        for _ in range(3):
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

    async def test_generic_error_message(self, mock_api, client, no_sleep):
        """Generic GraphQL error with no HTTP status is treated as transient
        and retried 3 times before surfacing.
        """
        _authenticate(client)
        for _ in range(3):
            mock_api.post(API_URL, body=_error_generic("Database timeout"))

        with pytest.raises(APIResponseError, match="Database timeout"):
            await client.list_installations()


# ---------------------------------------------------------------------------
# Transient retry behavior
# ---------------------------------------------------------------------------


class TestTransientRetry:
    """Retries absorb single-tick upstream blips inside one coordinator tick.

    See docs/findings/unavailable-flapping.md for the incident that
    motivated this behavior.
    """

    async def test_http_500_then_success(self, mock_api, client, no_sleep):
        """One 5xx blip is absorbed — caller sees only the success."""
        _authenticate(client)
        mock_api.post(API_URL, status=500, body="Internal Server Error")
        mock_api.post(API_URL, body=_installation_list())

        result = await client.list_installations()

        assert len(result) == 1
        assert result[0].number == INSTALLATION.number

    async def test_connection_error_then_success(
        self, mock_api, client, no_sleep
    ):
        """One TCP reset is absorbed — caller sees only the success."""
        _authenticate(client)
        mock_api.post(
            API_URL,
            exception=ClientConnectorError(
                connection_key=MagicMock(),
                os_error=OSError("Connection reset by peer"),
            ),
        )
        mock_api.post(API_URL, body=_installation_list())

        result = await client.list_installations()

        assert len(result) == 1

    async def test_graphql_js_bug_then_success(
        self, mock_api, client, no_sleep
    ):
        """The Mode B regression scenario: the Verisure backend JS undefined
        bug must be treated as transient and absorbed, not classified as
        credentials failure.
        """
        _authenticate(client)
        mock_api.post(
            API_URL,
            body=_error_generic("Cannot read properties of undefined (reading 'it')"),
        )
        mock_api.post(API_URL, body=_installation_list())

        result = await client.list_installations()

        assert len(result) == 1

    async def test_two_failures_then_success(self, mock_api, client, no_sleep):
        """Max retries = 3 attempts; two failures then success still works."""
        _authenticate(client)
        mock_api.post(API_URL, status=500, body="Server error")
        mock_api.post(API_URL, status=502, body="Bad gateway")
        mock_api.post(API_URL, body=_installation_list())

        result = await client.list_installations()

        assert len(result) == 1

    async def test_waf_not_retried(self, mock_api, client, no_sleep):
        """WAF blocking demands a cold-off — never retried."""
        _authenticate(client)
        mock_api.post(
            API_URL, status=403, body="_Incapsula_Resource blocked"
        )
        # If retry were attempted, next call would have no mock and fail

        with pytest.raises(WAFBlockedError):
            await client.list_installations()

    async def test_session_expired_not_retried(
        self, mock_api, client, no_sleep
    ):
        """Session expiry has its own recovery path — don't retry in-place."""
        _authenticate(client)
        mock_api.post(API_URL, body=_error_session_expired())

        with pytest.raises(SessionExpiredError):
            await client.list_installations()

    async def test_2fa_not_retried(self, mock_api, client, no_sleep):
        """2FA requires user action — never retried."""
        _authenticate(client)
        mock_api.post(API_URL, body=_error_2fa_required())

        with pytest.raises(TwoFactorRequiredError):
            await client.list_installations()

    async def test_4xx_not_retried(self, mock_api, client, no_sleep):
        """4xx client errors are not transient — not retried."""
        _authenticate(client)
        mock_api.post(API_URL, status=401, body="Unauthorized")
        # Only one mock registered — if retry were attempted, test would
        # raise a MockNotFound error from aioresponses

        with pytest.raises(APIResponseError, match="HTTP 401"):
            await client.list_installations()


# ---------------------------------------------------------------------------
# SessionExpired token invalidation
# ---------------------------------------------------------------------------


class TestSessionExpiredInvalidation:
    """When the server says the session is dead, cached auth + capabilities
    tokens must be nuked. Without this, _ensure_auth trusts the local JWT
    exp claim and keeps sending stale credentials forever (Mode C).

    See docs/findings/unavailable-flapping.md for the 2026-04-20 incident.
    """

    async def test_session_expired_clears_auth_and_capabilities(
        self, mock_api, client
    ):
        """SessionExpired on an installation-scoped operation clears both
        the auth token and the capabilities token for that installation.
        """
        _authenticate(client)
        assert client._auth_token is not None
        assert INSTALLATION.number in client._capabilities

        mock_api.post(API_URL, body=_error_session_expired())

        with pytest.raises(SessionExpiredError):
            await client.get_general_status(INSTALLATION)

        assert client._auth_token is None
        assert INSTALLATION.number not in client._capabilities
        assert INSTALLATION.number not in client._capabilities_exp

    async def test_session_expired_on_list_installations_clears_auth(
        self, mock_api, client
    ):
        """SessionExpired on a non-installation operation still clears auth
        (installation-less operations only rely on auth_token).
        """
        _authenticate(client)
        other_inst_number = "99999999"
        client._capabilities[other_inst_number] = "untouched-cap-token"

        mock_api.post(API_URL, body=_error_session_expired())

        with pytest.raises(SessionExpiredError):
            await client.list_installations()

        assert client._auth_token is None
        # Other installations' capabilities are not touched
        assert client._capabilities[other_inst_number] == "untouched-cap-token"


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
        client._auth_token_exp = datetime.min.replace(tzinfo=UTC)  # Already expired

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
        client._auth_token_exp = datetime.now(tz=UTC) + timedelta(hours=1)
        client._login_timestamp = int(datetime.now().timestamp() * 1000)
        # Capabilities expired
        client._capabilities_exp[INSTALLATION.number] = datetime.min.replace(tzinfo=UTC)

        cap_token = _make_jwt()
        mock_api.post(API_URL, body=_services_response(cap_token))
        mock_api.post(API_URL, body=_general_status("B"))

        status = await client.get_general_status(INSTALLATION)

        assert status.status == "B"
        assert client._capabilities[INSTALLATION.number] == cap_token


# ---------------------------------------------------------------------------
# Force arm tests
# ---------------------------------------------------------------------------


class TestForceArm:
    async def test_arm_raises_arming_exception_on_open_zone(self, mock_api, client):
        """Arm with open zone raises ArmingExceptionError with zone details."""
        _authenticate(client)
        mock_api.post(API_URL, body=_arm_panel_response())
        mock_api.post(API_URL, body=_arm_status_with_error())
        mock_api.post(API_URL, body=_get_exceptions_response())

        target = AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON)
        with pytest.raises(ArmingExceptionError) as exc_info:
            await client.arm(INSTALLATION, target)

        assert exc_info.value.reference_id == "error-ref-123"
        assert exc_info.value.suid == "error-suid-456"
        assert len(exc_info.value.exceptions) == 1
        assert exc_info.value.exceptions[0].alias == "finestracucina"

    async def test_force_arm_succeeds_with_remote_id(self, mock_api, client):
        """Force arm with forceArmingRemoteId completes successfully."""
        _authenticate(client)
        mock_api.post(API_URL, body=_arm_panel_response())
        mock_api.post(API_URL, body=_arm_status_complete("A"))

        target = AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON)
        result = await client.arm(
            INSTALLATION, target, force_arming_remote_id="error-ref-123"
        )

        assert result.proto_code == ProtoCode.TOTAL_PERIMETER

    async def test_arm_non_blocking_without_allow_forcing_raises_failed(
        self, mock_api, client
    ):
        """NON_BLOCKING error without allowForcing raises OperationFailedError."""
        _authenticate(client)
        mock_api.post(API_URL, body=_arm_panel_response())
        mock_api.post(API_URL, body=_arm_status_with_error(allow_forcing=False))

        target = AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON)
        with pytest.raises(OperationFailedError):
            await client.arm(INSTALLATION, target)

    async def test_get_exceptions_polls_through_wait(self, mock_api, client):
        """_get_exceptions polls until OK, not just first response."""
        _authenticate(client)
        mock_api.post(API_URL, body=_arm_panel_response())
        mock_api.post(API_URL, body=_arm_status_with_error())
        # First exceptions poll returns WAIT
        mock_api.post(API_URL, body=json.dumps({
            "data": {"xSGetExceptions": {"res": "WAIT", "msg": None, "exceptions": None}}
        }))
        # Second returns OK with data
        mock_api.post(API_URL, body=_get_exceptions_response())

        target = AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON)
        with pytest.raises(ArmingExceptionError) as exc_info:
            await client.arm(INSTALLATION, target)

        assert exc_info.value.exceptions[0].alias == "finestracucina"

    async def test_arm_multiple_open_zones(self, mock_api, client):
        """Multiple open zones reported in exception."""
        _authenticate(client)
        mock_api.post(API_URL, body=_arm_panel_response())
        mock_api.post(API_URL, body=_arm_status_with_error())
        mock_api.post(API_URL, body=_get_exceptions_response([
            {"status": "OPEN", "deviceType": "MAGNETIC", "alias": "finestracucina"},
            {"status": "OPEN", "deviceType": "MAGNETIC", "alias": "portaingresso"},
        ]))

        target = AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON)
        with pytest.raises(ArmingExceptionError) as exc_info:
            await client.arm(INSTALLATION, target)

        assert len(exc_info.value.exceptions) == 2
        aliases = [e.alias for e in exc_info.value.exceptions]
        assert "finestracucina" in aliases
        assert "portaingresso" in aliases


# ---------------------------------------------------------------------------
# Unknown proto code propagation (M20)
# ---------------------------------------------------------------------------


class TestUnknownProtoCode:
    """Verify that unknown proto codes propagate as UnexpectedStateError
    when the result is accessed — the client returns raw proto strings,
    the error fires on .proto_code / .alarm_state access."""

    async def test_unknown_proto_in_arm_result(self, mock_api, client):
        """arm() result with unknown proto raises on .alarm_state access."""
        _authenticate(client)
        mock_api.post(API_URL, body=_arm_panel_response())
        mock_api.post(API_URL, body=_arm_status_complete("Z"))

        target = AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON)
        result = await client.arm(INSTALLATION, target)

        # The client stores the raw proto — error fires on property access
        with pytest.raises(UnexpectedStateError) as exc_info:
            _ = result.alarm_state
        assert exc_info.value.proto_code == "Z"

    async def test_unknown_proto_in_disarm_result(self, mock_api, client):
        """disarm() result with unknown proto raises on .proto_code access."""
        _authenticate(client)
        client.set_last_proto("A")  # Armed — resolver must resolve before HTTP call.
        mock_api.post(API_URL, body=_disarm_panel_response())
        mock_api.post(API_URL, body=json.dumps({
            "data": {
                "xSDisarmStatus": {
                    "res": "OK",
                    "msg": "disarmed",
                    "status": "0",
                    "numinst": INSTALLATION.number,
                    "protomResponse": "Z",
                    "protomResponseDate": "2026-04-02T10:30:00",
                    "requestId": "req-123",
                    "error": None,
                }
            }
        }))

        result = await client.disarm(INSTALLATION)
        with pytest.raises(UnexpectedStateError) as exc_info:
            _ = result.proto_code
        assert exc_info.value.proto_code == "Z"


# ---------------------------------------------------------------------------
# validate_device / OTP flow (M19)
# ---------------------------------------------------------------------------


def _validate_device_otp_challenge() -> str:
    """Response when device needs OTP — returns error with auth hash + phones."""
    return json.dumps({
        "errors": [{
            "message": "Unauthorized",
            "data": {
                "status": 401,
                "auth-otp-hash": "otp-hash-abc",
                "auth-phones": [
                    {"id": 1, "phone": "+39333***1234"},
                    {"id": 2, "phone": "+39333***5678"},
                ],
            },
        }]
    })


def _validate_device_success() -> str:
    """Response when device validation succeeds (Verisure IT: hash=null)."""
    return json.dumps({
        "data": {
            "xSValidateDevice": {
                "res": "OK",
                "msg": "Device validated",
                "hash": None,
                "refreshToken": None,
            }
        }
    })


def _validate_device_success_with_token() -> str:
    """Response when device validation returns a new auth token."""
    return json.dumps({
        "data": {
            "xSValidateDevice": {
                "res": "OK",
                "msg": "Device validated",
                "hash": _make_jwt(),
                "refreshToken": "refresh-new",
            }
        }
    })


class TestValidateDevice:
    async def test_otp_challenge_returns_hash_and_phones(self, mock_api, client):
        """First call returns OTP hash + phone list for user selection."""
        mock_api.post(API_URL, body=_validate_device_otp_challenge())

        otp_hash, phones = await client.validate_device(None, None)

        assert otp_hash == "otp-hash-abc"
        assert len(phones) == 2
        assert phones[0].id == 1
        assert phones[1].phone == "+39333***5678"

    async def test_successful_validation_returns_none_hash(self, mock_api, client):
        """Successful validation with hash=null (Verisure IT flow)."""
        mock_api.post(API_URL, body=_validate_device_success())

        otp_hash, phones = await client.validate_device("otp-hash", "123456")

        assert otp_hash is None
        assert phones == []

    async def test_successful_validation_stores_token(self, mock_api, client):
        """Successful validation with a token updates auth state."""
        mock_api.post(API_URL, body=_validate_device_success_with_token())

        await client.validate_device("otp-hash", "123456")

        assert client._auth_token is not None

    async def test_api_error_raises_authentication_error(self, mock_api, client):
        """API error during validation becomes AuthenticationError."""
        mock_api.post(API_URL, body=_error_generic("Validation failed"), status=200)

        with pytest.raises(AuthenticationError):
            await client.validate_device("otp-hash", "wrong-code")


# ---------------------------------------------------------------------------
# CommandResolver wire-in — arm() picks commands from current state + services
# ---------------------------------------------------------------------------


class TestArmResolverWireIn:
    """Arm path uses CommandResolver with current state from _last_proto."""

    async def test_arm_total_from_partial_uses_transition_command(
        self, mock_api, client,
    ):
        """Arming TOTAL while currently PARTIAL sends ARMINTFPART1, not ARM1."""
        _authenticate(client)
        client.set_last_proto("P")  # currently PARTIAL interior
        mock_api.post(API_URL, body=_arm_panel_response())
        mock_api.post(API_URL, body=_arm_status_complete("T"))

        target = AlarmState(
            interior=InteriorMode.TOTAL, perimeter=PerimeterMode.OFF,
        )
        result = await client.arm(INSTALLATION, target)

        arm_call = _find_call_with_operation(mock_api, "xSArmPanel")
        body = arm_call.kwargs["json"]
        assert body["variables"]["request"] == "ARMINTFPART1"
        assert result.proto_code == ProtoCode.TOTAL

    async def test_arm_total_perimeter_from_off_uses_base_command(
        self, mock_api, client,
    ):
        """Arming TOTAL+PERI from disarmed sends ARM1PERI1."""
        _authenticate(client)
        client.set_last_proto("D")
        mock_api.post(API_URL, body=_arm_panel_response())
        mock_api.post(API_URL, body=_arm_status_complete("A"))

        target = AlarmState(
            interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON,
        )
        await client.arm(INSTALLATION, target)

        arm_call = _find_call_with_operation(mock_api, "xSArmPanel")
        body = arm_call.kwargs["json"]
        assert body["variables"]["request"] == "ARM1PERI1"

    async def test_arm_refuses_peri_target_on_interior_only_panel(
        self, mock_api, client,
    ):
        """SDVFAST can't do perimeter arm — refuse locally, zero bytes sent."""
        from verisure_italy.exceptions import UnsupportedCommandError

        _authenticate(client)
        # Swap to an interior-only panel
        sdvfast_installation = INSTALLATION.model_copy(
            update={"panel": "SDVFAST"}
        )
        # Seed SDVFAST's actual active services (no PERI)
        client._services_cache[INSTALLATION.number] = frozenset({
            ServiceRequest.ARM,
            ServiceRequest.DARM,
            ServiceRequest.ARMDAY,
            ServiceRequest.ARMNIGHT,
            ServiceRequest.ARMINTFPART,
            ServiceRequest.ARMPARTFINT,
        })
        client.set_last_proto("D")

        target = AlarmState(
            interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON,
        )
        with pytest.raises(UnsupportedCommandError) as exc:
            await client.arm(sdvfast_installation, target)

        assert ServiceRequest.PERI in exc.value.missing_services
        # Zero bytes sent to the panel — no xSArmPanel mutation posted.
        assert not _has_call_with_operation(mock_api, "xSArmPanel")

    async def test_arm_without_current_state_raises(
        self, mock_api, client,
    ):
        """No _last_proto observation yet — arm refuses, no HTTP call."""
        _authenticate(client)
        # Undo the _authenticate seeding — simulate a freshly-started
        # client that hasn't yet polled xSStatus.
        client._last_proto = ""

        target = AlarmState(
            interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON,
        )
        with pytest.raises(StateNotObservedError, match="no current-state observation"):
            await client.arm(INSTALLATION, target)

        assert not _has_call_with_operation(mock_api, "xSArmPanel")


# ---------------------------------------------------------------------------
# CommandResolver wire-in — disarm() picks DARM1 vs DARM1DARMPERI
# ---------------------------------------------------------------------------


class TestDisarmResolverWireIn:
    """Disarm path uses CommandResolver to pick DARM1 vs DARM1DARMPERI."""

    async def test_disarm_sdvfast_sends_darm1_not_darm1darmperi(
        self, mock_api, client,
    ):
        """Interior-only panel: DARM1 regardless of apparent proto state."""
        _authenticate(client)
        # SDVFAST — interior-only panel, no PERI service active.
        sdvfast_installation = INSTALLATION.model_copy(update={"panel": "SDVFAST"})
        client._services_cache[INSTALLATION.number] = frozenset({
            ServiceRequest.ARM, ServiceRequest.DARM, ServiceRequest.ARMDAY,
            ServiceRequest.ARMNIGHT, ServiceRequest.ARMINTFPART,
            ServiceRequest.ARMPARTFINT,
        })
        # Even if _last_proto is "T" (total) — no perimeter possible on this family.
        client.set_last_proto("T")
        mock_api.post(API_URL, body=_disarm_panel_response())
        mock_api.post(API_URL, body=_disarm_status_complete())

        await client.disarm(sdvfast_installation)

        disarm_call = _find_call_with_operation(mock_api, "xSDisarmPanel")
        body = disarm_call.kwargs["json"]
        assert body["variables"]["request"] == "DARM1"

    async def test_disarm_sdvecu_from_total_peri_sends_disarm_all(
        self, mock_api, client,
    ):
        """Peri-capable panel arming total+peri: DARM1DARMPERI."""
        _authenticate(client)
        client.set_last_proto("A")  # total + peri (SDVECU)
        mock_api.post(API_URL, body=_disarm_panel_response())
        mock_api.post(API_URL, body=_disarm_status_complete())

        await client.disarm(INSTALLATION)

        disarm_call = _find_call_with_operation(mock_api, "xSDisarmPanel")
        body = disarm_call.kwargs["json"]
        assert body["variables"]["request"] == "DARM1DARMPERI"

    async def test_disarm_sdvecu_from_total_only_sends_darm1(
        self, mock_api, client,
    ):
        """Peri-capable panel, but perimeter currently OFF: DARM1 is enough."""
        _authenticate(client)
        client.set_last_proto("T")  # total only, perimeter OFF
        mock_api.post(API_URL, body=_disarm_panel_response())
        mock_api.post(API_URL, body=_disarm_status_complete())

        await client.disarm(INSTALLATION)

        disarm_call = _find_call_with_operation(mock_api, "xSDisarmPanel")
        body = disarm_call.kwargs["json"]
        assert body["variables"]["request"] == "DARM1"

    async def test_disarm_without_current_state_raises(
        self, mock_api, client,
    ):
        """No _last_proto observation yet — disarm refuses, no HTTP call."""
        _authenticate(client)
        client._last_proto = ""
        with pytest.raises(StateNotObservedError):
            await client.disarm(INSTALLATION)
        assert not _has_call_with_operation(mock_api, "xSDisarmPanel")


# ---------------------------------------------------------------------------
# Services cache
# ---------------------------------------------------------------------------


class TestServicesCache:
    """Services cache behaviour: populate on first use, clear on auth rotation."""

    async def test_second_arm_does_not_refetch_services(
        self, mock_api, client,
    ):
        """Cache hit on second arm — no extra xSSrv round-trip."""
        _authenticate(client)
        # _authenticate seeds the cache; arm should use it, not fetch.
        # We do not mock any xSSrv response here — if arm tried to fetch,
        # aioresponses would return a 404-ish error and the test would fail.
        mock_api.post(API_URL, body=_arm_panel_response())
        mock_api.post(API_URL, body=_arm_status_complete("A"))
        mock_api.post(API_URL, body=_arm_panel_response())
        mock_api.post(API_URL, body=_arm_status_complete("B"))

        target_away = AlarmState(interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON)
        await client.arm(INSTALLATION, target_away)

        # Reset current state to "disarmed" so the resolver accepts the next arm.
        client.set_last_proto("D")

        target_night = AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON)
        await client.arm(INSTALLATION, target_night)

        # Zero xSSrv calls should have been made.
        assert not _has_call_with_operation(mock_api, "Srv")

    async def test_cache_cleared_on_capabilities_rotation(
        self, mock_api, client,
    ):
        """When capabilities refresh path fires, services cache is invalidated."""
        _authenticate(client)
        # Seed a value in the cache (already done by _authenticate).
        assert INSTALLATION.number in client._services_cache

        # Force the capabilities-refresh path: expire the capabilities token.
        client._capabilities_exp[INSTALLATION.number] = datetime.min.replace(tzinfo=UTC)

        # The next get_services call will rotate the JWT; our invalidation
        # hook must clear the services cache in the same step.
        cap_token = _make_jwt()
        mock_api.post(API_URL, body=_services_response(cap_token))

        await client.get_services(INSTALLATION)

        # After the refresh, the cache for this installation must be gone.
        assert INSTALLATION.number not in client._services_cache
