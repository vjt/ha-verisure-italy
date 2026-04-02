# Verisure IT Home Assistant Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `custom_components/verisure_it/` — a Home Assistant alarm control panel integration backed by the existing `verisure_api` client library.

**Architecture:** DataUpdateCoordinator polls xSStatus every 5s for passive alarm state. AlarmControlPanelEntity maps our 6-state model to HA's alarm states. Config flow handles login + 2FA + installation selection. Force-arm flow handles open zone exceptions with event-driven automation support.

**Tech Stack:** Python 3.12, Home Assistant Core 2026.2+, Pydantic v2, aiohttp, pytest + aioresponses

**Spec:** `docs/superpowers/specs/2026-04-02-ha-integration-design.md`

---

## File Structure

```
Files to CREATE:
  custom_components/verisure_it/manifest.json
  custom_components/verisure_it/__init__.py
  custom_components/verisure_it/const.py
  custom_components/verisure_it/config_flow.py
  custom_components/verisure_it/coordinator.py
  custom_components/verisure_it/alarm_control_panel.py
  custom_components/verisure_it/strings.json
  custom_components/verisure_it/translations/en.json
  tests/test_state_mapping.py
  tests/test_force_arm.py

Files to MODIFY:
  verisure_api/models.py        — add ZoneException, update GeneralStatus, PanelError
  verisure_api/exceptions.py    — add ArmingExceptionError
  verisure_api/graphql.py       — add GET_EXCEPTIONS_QUERY, update ARM queries
  verisure_api/client.py        — add force_arming_remote_id to arm(), add _get_exceptions()
  verisure_api/responses.py     — add GetExceptionsEnvelope, update GeneralStatusEnvelope
  verisure_api/__init__.py      — export new types
  tests/test_client.py          — add force-arm tests
```

---

### Task 1: API Client — ZoneException Model + GeneralStatus Exceptions

**Files:**
- Modify: `verisure_api/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing test for ZoneException**

Add to `tests/test_models.py`:

```python
class TestZoneException:
    def test_parse_from_api_json(self):
        data = {"status": "OPEN", "deviceType": "MAGNETIC", "alias": "finestracucina"}
        exc = ZoneException.model_validate(data)
        assert exc.status == "OPEN"
        assert exc.device_type == "MAGNETIC"
        assert exc.alias == "finestracucina"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_models.py::TestZoneException -v`
Expected: FAIL — `ImportError: cannot import name 'ZoneException'`

- [ ] **Step 3: Implement ZoneException model**

Add to `verisure_api/models.py` after `PanelError`:

```python
class ZoneException(BaseModel):
    """An open zone reported during arming (from xSGetExceptions)."""

    status: str
    device_type: str = Field(alias="deviceType")
    alias: str
```

- [ ] **Step 4: Add suid to PanelError**

In `verisure_api/models.py`, update `PanelError`:

```python
class PanelError(BaseModel):
    """Error details from the panel (returned in arm/disarm status)."""

    code: str | None = None
    type: str | None = None
    allow_forcing: bool | None = Field(None, alias="allowForcing")
    exceptions_number: int | None = Field(None, alias="exceptionsNumber")
    reference_id: str | None = Field(None, alias="referenceId")
    suid: str | None = None
```

- [ ] **Step 5: Add exceptions to GeneralStatus**

In `verisure_api/models.py`, update `GeneralStatus`:

```python
class GeneralStatus(BaseModel):
    """Result of xSStatus — passive status query that doesn't ping the panel."""

    status: str
    timestamp_update: str = Field(alias="timestampUpdate")
    exceptions: list[ZoneException] | None = None
```

- [ ] **Step 6: Export ZoneException from __init__.py**

Add `ZoneException` to the imports in `verisure_api/__init__.py` and to `__all__`.

- [ ] **Step 7: Run all tests**

Run: `source .venv/bin/activate && pytest tests/ -x -q`
Expected: all pass (existing tests unaffected, new test passes)

- [ ] **Step 8: Run type checker**

Run: `source .venv/bin/activate && pyright verisure_api/`
Expected: 0 errors

- [ ] **Step 9: Commit**

```bash
git add verisure_api/models.py verisure_api/__init__.py tests/test_models.py
git commit -m "feat: add ZoneException model, suid to PanelError, exceptions to GeneralStatus"
```

---

### Task 2: API Client — ArmingExceptionError + GetExceptions Query

**Files:**
- Modify: `verisure_api/exceptions.py`
- Modify: `verisure_api/graphql.py`
- Modify: `verisure_api/responses.py`
- Modify: `verisure_api/__init__.py`

- [ ] **Step 1: Add ArmingExceptionError**

Add to `verisure_api/exceptions.py`:

```python
from .models import ZoneException


class ArmingExceptionError(VerisureError):
    """Arming blocked by open zones (NON_BLOCKING with allowForcing).

    Carries force-arm context so the caller can retry with
    forceArmingRemoteId to override the exception.
    """

    def __init__(
        self,
        reference_id: str,
        suid: str,
        exceptions: list[ZoneException],
    ) -> None:
        details = ", ".join(e.alias for e in exceptions)
        super().__init__(f"Arming blocked by open zones: {details}")
        self.reference_id = reference_id
        self.suid = suid
        self.exceptions = exceptions
```

Note: this creates a circular import risk (exceptions imports models). If pyright complains, use `from __future__ import annotations` and `TYPE_CHECKING`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import ZoneException
```

Then the runtime import happens in `__init__` body instead. Choose whichever approach pyright accepts.

- [ ] **Step 2: Add GET_EXCEPTIONS_QUERY to graphql.py**

Add to `verisure_api/graphql.py`:

```python
GET_EXCEPTIONS_QUERY = (
    "query xSGetExceptions("
    "$numinst: String!, $panel: String!, "
    "$referenceId: String!, $counter: Int!, $suid: String"
    ") { xSGetExceptions("
    "numinst: $numinst, panel: $panel, "
    "referenceId: $referenceId, counter: $counter, suid: $suid"
    ") { res msg"
    " exceptions { status deviceType alias } } }"
)
```

- [ ] **Step 3: Update ARM_PANEL_MUTATION with forceArmingRemoteId**

Replace the existing `ARM_PANEL_MUTATION` in `verisure_api/graphql.py`:

```python
ARM_PANEL_MUTATION = (
    "mutation xSArmPanel("
    "$numinst: String!, $request: ArmCodeRequest!, $panel: String!, "
    "$currentStatus: String, $suid: String, "
    "$forceArmingRemoteId: String"
    ") { xSArmPanel("
    "numinst: $numinst, request: $request, panel: $panel, "
    "currentStatus: $currentStatus, suid: $suid, "
    "forceArmingRemoteId: $forceArmingRemoteId"
    ") { res msg referenceId } }"
)
```

- [ ] **Step 4: Update ARM_STATUS_QUERY with forceArmingRemoteId + suid in error**

Replace the existing `ARM_STATUS_QUERY` in `verisure_api/graphql.py`:

```python
ARM_STATUS_QUERY = (
    "query ArmStatus("
    "$numinst: String!, $request: ArmCodeRequest, $panel: String!, "
    "$referenceId: String!, $counter: Int!, "
    "$forceArmingRemoteId: String"
    ") { xSArmStatus("
    "numinst: $numinst, panel: $panel, referenceId: $referenceId, "
    "counter: $counter, request: $request, "
    "forceArmingRemoteId: $forceArmingRemoteId"
    ") { res msg status protomResponse protomResponseDate numinst requestId"
    " error { code type allowForcing exceptionsNumber referenceId suid } } }"
)
```

- [ ] **Step 5: Add GetExceptionsEnvelope to responses.py**

Add to `verisure_api/responses.py`:

```python
from .models import ZoneException

class _GetExceptionsResult(BaseModel):
    res: str
    msg: str | None
    exceptions: list[ZoneException] | None = None


class GetExceptionsEnvelope(BaseModel):
    """Response from xSGetExceptions."""

    class Data(BaseModel):
        xSGetExceptions: _GetExceptionsResult  # noqa: N815

    data: Data
```

- [ ] **Step 6: Export new types from __init__.py**

Add `ArmingExceptionError` to `verisure_api/__init__.py` imports and `__all__`.
Add `GetExceptionsEnvelope` is internal (not exported — only used by client).

- [ ] **Step 7: Run type checker and linter**

Run: `source .venv/bin/activate && pyright verisure_api/ && ruff check verisure_api/`
Expected: 0 errors

- [ ] **Step 8: Commit**

```bash
git add verisure_api/exceptions.py verisure_api/graphql.py verisure_api/responses.py verisure_api/__init__.py
git commit -m "feat: add ArmingExceptionError, GET_EXCEPTIONS_QUERY, update ARM queries for force-arm"
```

---

### Task 3: API Client — Force-Arm Logic in client.py

**Files:**
- Modify: `verisure_api/client.py`
- Test: `tests/test_client.py`

- [ ] **Step 1: Write failing test for force-arm exception detection**

Add to `tests/test_client.py` a helper and test class:

```python
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
        mock_api.post(API_URL, body=_arm_status_ok("A"))

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
        mock_api.post(
            API_URL,
            body=_arm_status_with_error(allow_forcing=False),
        )

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
```

Add `ArmingExceptionError` to the imports at the top of the test file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_client.py::TestForceArm -v`
Expected: FAIL — `arm()` does not accept `force_arming_remote_id`, no `ArmingExceptionError` raised

- [ ] **Step 3: Implement _get_exceptions method**

Add to `verisure_api/client.py` after `_check_arm_status_once`:

```python
async def _get_exceptions(
    self,
    installation: Installation,
    reference_id: str,
    suid: str,
) -> list[ZoneException]:
    """Fetch arming exception details (open zones).

    Polls xSGetExceptions until OK or timeout. Returns zone list.
    """
    counter = 1
    max_polls = max(10, round(self._poll_timeout / max(1, self._poll_delay)))

    while counter <= max_polls:
        content: GraphQLContent = {
            "operationName": "xSGetExceptions",
            "variables": {
                "numinst": installation.number,
                "panel": installation.panel,
                "referenceId": reference_id,
                "counter": counter,
                "suid": suid,
            },
            "query": GET_EXCEPTIONS_QUERY,
        }
        response_text = await self._execute(
            content, "xSGetExceptions", installation
        )
        envelope = GetExceptionsEnvelope.model_validate_json(response_text)
        result = envelope.data.xSGetExceptions

        if result.res == "OK":
            return result.exceptions or []

        if result.res != "WAIT":
            _LOGGER.warning(
                "Unexpected xSGetExceptions result: %s", result.res
            )
            return []

        await asyncio.sleep(self._poll_delay)
        counter += 1

    _LOGGER.warning(
        "Failed to fetch exceptions after %d polls", max_polls
    )
    return []
```

Add the necessary imports at the top of `client.py`:

```python
from .graphql import GET_EXCEPTIONS_QUERY  # add to existing import
from .models import ZoneException  # add to existing import
from .responses import GetExceptionsEnvelope  # add to existing import
from .exceptions import ArmingExceptionError  # add to existing import
```

- [ ] **Step 4: Update _check_arm_status_once to return ArmResult directly**

The current `_check_arm_status_once` converts `ArmResult` to `OperationResult`, which drops the `error` field. We need to keep the error for force-arm detection. Change the arm polling to work with `ArmResult` directly.

Replace `_check_arm_status_once`:

```python
async def _check_arm_status_once(
    self,
    installation: Installation,
    reference_id: str,
    counter: int,
    command: ArmCommand,
    force_arming_remote_id: str | None = None,
) -> OperationResult:
    """Single arm status poll."""
    variables: GraphQLVars = {
        "request": command.value,
        "numinst": installation.number,
        "panel": installation.panel,
        "currentStatus": self._last_proto,
        "referenceId": reference_id,
        "counter": counter,
    }
    if force_arming_remote_id is not None:
        variables["forceArmingRemoteId"] = force_arming_remote_id

    content: GraphQLContent = {
        "operationName": "ArmStatus",
        "variables": variables,
        "query": ARM_STATUS_QUERY,
    }
    response_text = await self._execute(
        content, "ArmStatus", installation
    )
    envelope = ArmStatusEnvelope.model_validate_json(response_text)
    arm_result = envelope.data.xSArmStatus

    # Detect force-arm-eligible error BEFORE converting to OperationResult
    if arm_result.res == "ERROR" and arm_result.error is not None:
        if (
            arm_result.error.type == "NON_BLOCKING"
            and arm_result.error.allow_forcing
            and arm_result.error.reference_id is not None
        ):
            suid = arm_result.error.suid or ""
            exceptions = await self._get_exceptions(
                installation, arm_result.error.reference_id, suid
            )
            raise ArmingExceptionError(
                arm_result.error.reference_id, suid, exceptions
            )

    # Return as OperationResult for the generic poll machinery
    return OperationResult(
        res=arm_result.res,
        msg=arm_result.msg,
        status=arm_result.status,
        numinst=arm_result.numinst,
        protomResponse=arm_result.protom_response,
        protomResponseDate=arm_result.protom_response_data,
    )
```

- [ ] **Step 5: Update arm() to accept force_arming_remote_id**

Replace the `arm` method:

```python
async def arm(
    self,
    installation: Installation,
    target_state: AlarmState,
    force_arming_remote_id: str | None = None,
) -> ArmResult:
    """Arm the alarm. Polls until complete.

    Returns ArmResult or raises OperationTimeoutError/OperationFailedError.
    Raises ArmingExceptionError if open zones detected (NON_BLOCKING with
    allowForcing). Caller can retry with force_arming_remote_id from the
    exception to override.
    """
    command = STATE_TO_COMMAND[target_state]
    await self._ensure_auth(installation)

    variables: GraphQLVars = {
        "request": command.value,
        "numinst": installation.number,
        "panel": installation.panel,
        "currentStatus": self._last_proto,
    }
    if force_arming_remote_id is not None:
        variables["forceArmingRemoteId"] = force_arming_remote_id

    content: GraphQLContent = {
        "operationName": "xSArmPanel",
        "variables": variables,
        "query": ARM_PANEL_MUTATION,
    }
    response_text = await self._execute(
        content, "xSArmPanel", installation
    )
    envelope = ArmPanelEnvelope.model_validate_json(response_text)
    arm_resp = envelope.data.xSArmPanel

    if arm_resp.res != "OK":
        raise OperationFailedError(
            f"Arm rejected: {arm_resp.msg}",
            error_code=None,
            error_type=None,
        )

    poll_fn = partial(
        self._check_arm_status_once,
        command=command,
        force_arming_remote_id=force_arming_remote_id,
    )
    result = await self._poll_operation(
        installation, arm_resp.reference_id, poll_fn
    )

    if result.protom_response is None or result.protom_response_data is None:
        raise APIResponseError(
            "Arm completed but response missing proto fields",
            http_status=None,
        )

    self._last_proto = result.protom_response
    return ArmResult(
        res=result.res,
        msg=result.msg,
        status=result.status,
        numinst=result.numinst,
        protomResponse=result.protom_response,
        protomResponseDate=result.protom_response_data,
        requestId="",
        error=None,
    )
```

Note: `_check_arm_status_once` now takes `force_arming_remote_id` as a keyword arg, and `partial()` passes it through. The `PollFn` type alias will need updating — the generic poll machinery calls `poll_fn(installation, reference_id, counter)`, so `force_arming_remote_id` is baked in via `partial`.

- [ ] **Step 6: Update PollFn and _poll_operation for ArmingExceptionError**

The `_poll_operation` currently catches `result.res == "ERROR"` and raises `OperationFailedError`. But now `_check_arm_status_once` raises `ArmingExceptionError` BEFORE returning an ERROR result. So `_poll_operation` never sees the NON_BLOCKING+allowForcing case — it propagates naturally. No change needed to `_poll_operation`.

However, the `partial()` call to `_check_arm_status_once` now includes a keyword arg not in the `PollFn` signature. Since `partial` bakes in the extra kwarg, the callable still matches `PollFn = Callable[[Installation, str, int], Awaitable[OperationResult]]`. Verify pyright is happy.

- [ ] **Step 7: Run all tests**

Run: `source .venv/bin/activate && pytest tests/ -x -q`
Expected: all pass including new force-arm tests

- [ ] **Step 8: Run type checker and linter**

Run: `source .venv/bin/activate && pyright verisure_api/ && ruff check verisure_api/ tests/`
Expected: 0 errors

- [ ] **Step 9: Commit**

```bash
git add verisure_api/client.py verisure_api/graphql.py verisure_api/responses.py tests/test_client.py
git commit -m "feat: force-arm support — detect open zones, raise ArmingExceptionError, pass forceArmingRemoteId"
```

---

### Task 4: HA Integration — manifest.json + const.py

**Files:**
- Create: `custom_components/verisure_it/manifest.json`
- Create: `custom_components/verisure_it/const.py`
- Modify: `custom_components/verisure_it/__init__.py` (replace placeholder)

- [ ] **Step 1: Create manifest.json**

```json
{
  "domain": "verisure_it",
  "name": "Verisure Italy",
  "codeowners": ["@vjt"],
  "config_flow": true,
  "dependencies": [],
  "documentation": "https://github.com/vjt/ha-verisure",
  "iot_class": "cloud_polling",
  "requirements": ["aiohttp>=3.9", "pydantic>=2.0", "pyjwt>=2.8"],
  "version": "0.3.0"
}
```

- [ ] **Step 2: Create const.py**

```python
"""Constants for the Verisure Italy integration."""

DOMAIN = "verisure_it"

CONF_INSTALLATION_NUMBER = "installation_number"
CONF_INSTALLATION_PANEL = "installation_panel"
CONF_INSTALLATION_ALIAS = "installation_alias"
CONF_DEVICE_ID = "device_id"
CONF_UUID = "uuid"

DEFAULT_POLL_INTERVAL = 5
```

- [ ] **Step 3: Commit**

```bash
git add custom_components/verisure_it/manifest.json custom_components/verisure_it/const.py
git commit -m "feat: verisure_it integration manifest and constants"
```

---

### Task 5: HA Integration — State Mapping Tests + Module

**Files:**
- Create: `tests/test_state_mapping.py`

This task tests the state mapping logic in isolation before building the entity.

- [ ] **Step 1: Write state mapping tests**

Create `tests/test_state_mapping.py`:

```python
"""Tests for AlarmState -> HA AlarmControlPanelState mapping.

These test the mapping logic that the alarm_control_panel entity will use.
We test the pure mapping function in isolation.
"""

import pytest

from verisure_api.models import (
    PROTO_TO_STATE,
    AlarmState,
    InteriorMode,
    PerimeterMode,
    ProtoCode,
)


# The mapping function that the entity will use.
# Defined here first, moved to the integration module in Task 7.
def map_alarm_state(state: AlarmState) -> str:
    """Map our two-axis AlarmState to HA AlarmControlPanelState string.

    Returns the HA state string value. Uses strings instead of the HA enum
    so tests can run without homeassistant installed.
    """
    DISARMED = AlarmState(interior=InteriorMode.OFF, perimeter=PerimeterMode.OFF)
    PARTIAL_PERIMETER = AlarmState(
        interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON
    )
    TOTAL_PERIMETER = AlarmState(
        interior=InteriorMode.TOTAL, perimeter=PerimeterMode.ON
    )

    if state == DISARMED:
        return "disarmed"
    if state == PARTIAL_PERIMETER:
        return "armed_home"
    if state == TOTAL_PERIMETER:
        return "armed_away"
    # All other states: display as custom bypass
    return "armed_custom_bypass"


class TestStateMapping:
    def test_disarmed(self):
        state = PROTO_TO_STATE[ProtoCode.DISARMED]
        assert map_alarm_state(state) == "disarmed"

    def test_partial_perimeter_is_armed_home(self):
        state = PROTO_TO_STATE[ProtoCode.PARTIAL_PERIMETER]
        assert map_alarm_state(state) == "armed_home"

    def test_total_perimeter_is_armed_away(self):
        state = PROTO_TO_STATE[ProtoCode.TOTAL_PERIMETER]
        assert map_alarm_state(state) == "armed_away"

    def test_perimeter_only_is_custom_bypass(self):
        state = PROTO_TO_STATE[ProtoCode.PERIMETER_ONLY]
        assert map_alarm_state(state) == "armed_custom_bypass"

    def test_partial_no_perimeter_is_custom_bypass(self):
        state = PROTO_TO_STATE[ProtoCode.PARTIAL]
        assert map_alarm_state(state) == "armed_custom_bypass"

    def test_total_no_perimeter_is_custom_bypass(self):
        state = PROTO_TO_STATE[ProtoCode.TOTAL]
        assert map_alarm_state(state) == "armed_custom_bypass"

    def test_all_six_proto_codes_mapped(self):
        """Every proto code has a mapping — no gaps."""
        for code in ProtoCode:
            state = PROTO_TO_STATE[code]
            result = map_alarm_state(state)
            assert result in {
                "disarmed", "armed_home", "armed_away", "armed_custom_bypass"
            }, f"Proto {code} mapped to unexpected {result}"
```

- [ ] **Step 2: Run tests**

Run: `source .venv/bin/activate && pytest tests/test_state_mapping.py -v`
Expected: all 7 pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_state_mapping.py
git commit -m "test: state mapping — AlarmState to HA AlarmControlPanelState"
```

---

### Task 6: HA Integration — Coordinator

**Files:**
- Create: `custom_components/verisure_it/coordinator.py`

Note: This and subsequent HA integration files cannot be unit tested without homeassistant installed as a dependency. Integration testing happens via deploy to the live HA instance. The state mapping tests (Task 5) cover the pure logic.

- [ ] **Step 1: Create coordinator.py**

```python
"""DataUpdateCoordinator for Verisure Italy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from aiohttp import ClientSession

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from verisure_api import (
    AlarmState,
    AuthenticationError,
    GeneralStatus,
    Installation,
    ProtoCode,
    SessionExpiredError,
    VerisureClient,
    WAFBlockedError,
    generate_device_id,
    generate_uuid,
    parse_proto_code,
)
from verisure_api.exceptions import APIConnectionError, UnexpectedStateError
from verisure_api.models import PROTO_TO_STATE, ZoneException

from .const import (
    CONF_DEVICE_ID,
    CONF_INSTALLATION_ALIAS,
    CONF_INSTALLATION_NUMBER,
    CONF_INSTALLATION_PANEL,
    CONF_UUID,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerisureStatusData:
    """Data returned by the coordinator."""

    alarm_state: AlarmState
    proto_code: ProtoCode
    timestamp: str
    exceptions: list[ZoneException]


class VerisureCoordinator(DataUpdateCoordinator[VerisureStatusData]):
    """Coordinator that polls xSStatus for passive alarm state."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        poll_interval = config_entry.options.get(
            "poll_interval", DEFAULT_POLL_INTERVAL
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
            config_entry=config_entry,
        )

        self._session = ClientSession()
        self.client = VerisureClient(
            username=config_entry.data[CONF_USERNAME],
            password=config_entry.data[CONF_PASSWORD],
            http_session=self._session,
            device_id=config_entry.data[CONF_DEVICE_ID],
            uuid=config_entry.data[CONF_UUID],
            id_device_indigitall="",
        )
        self.installation = Installation(
            numinst=config_entry.data[CONF_INSTALLATION_NUMBER],
            alias=config_entry.data[CONF_INSTALLATION_ALIAS],
            panel=config_entry.data[CONF_INSTALLATION_PANEL],
            type="",
            name="",
            surname="",
            address="",
            city="",
            postcode="",
            province="",
            email="",
            phone="",
        )

    async def async_shutdown(self) -> None:
        """Close the HTTP session."""
        await super().async_shutdown()
        await self._session.close()

    async def _async_update_data(self) -> VerisureStatusData:
        """Poll xSStatus for current alarm state."""
        try:
            status: GeneralStatus = await self.client.get_general_status(
                self.installation
            )
        except SessionExpiredError:
            _LOGGER.debug("Session expired, re-authenticating")
            try:
                await self.client.login()
                status = await self.client.get_general_status(
                    self.installation
                )
            except AuthenticationError as err:
                raise ConfigEntryAuthFailed(
                    f"Re-authentication failed: {err.message}"
                ) from err
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(err.message) from err
        except (APIConnectionError, WAFBlockedError) as err:
            raise UpdateFailed(err.message) from err
        except UnexpectedStateError as err:
            _LOGGER.error("Unexpected alarm state: %s", err.proto_code)
            raise UpdateFailed(err.message) from err

        proto = parse_proto_code(status.status)
        alarm_state = PROTO_TO_STATE[proto]

        return VerisureStatusData(
            alarm_state=alarm_state,
            proto_code=proto,
            timestamp=status.timestamp_update,
            exceptions=status.exceptions or [],
        )
```

- [ ] **Step 2: Run type checker on the file**

Run: `source .venv/bin/activate && pyright custom_components/verisure_it/coordinator.py`

Note: This will likely fail because homeassistant is not installed in the dev venv. That's expected — we validate on the live HA instance. Verify there are no obvious typos or import errors by reading the output.

- [ ] **Step 3: Commit**

```bash
git add custom_components/verisure_it/coordinator.py
git commit -m "feat: VerisureCoordinator — polls xSStatus for passive alarm state"
```

---

### Task 7: HA Integration — Alarm Control Panel Entity

**Files:**
- Create: `custom_components/verisure_it/alarm_control_panel.py`

- [ ] **Step 1: Create alarm_control_panel.py**

```python
"""Alarm control panel entity for Verisure Italy."""

from __future__ import annotations

import datetime
import logging
from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from verisure_api import (
    ArmResult,
    DisarmResult,
    OperationFailedError,
    OperationTimeoutError,
)
from verisure_api.exceptions import ArmingExceptionError
from verisure_api.models import (
    PROTO_TO_STATE,
    AlarmState,
    InteriorMode,
    PerimeterMode,
    ProtoCode,
)

from .const import DOMAIN
from .coordinator import VerisureCoordinator, VerisureStatusData

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
    # Non-primary states — display as custom bypass
    PROTO_TO_STATE[ProtoCode.PERIMETER_ONLY]: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
    PROTO_TO_STATE[ProtoCode.PARTIAL]: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
    PROTO_TO_STATE[ProtoCode.TOTAL]: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
}

# Notification ID for arming exceptions
_NOTIFICATION_ID_PREFIX = f"{DOMAIN}.arming_exception"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the alarm control panel from a config entry."""
    coordinator: VerisureCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([VerisureAlarmPanel(coordinator)])


class VerisureAlarmPanel(
    CoordinatorEntity[VerisureCoordinator], AlarmControlPanelEntity
):
    """Alarm control panel for Verisure Italy."""

    _attr_has_entity_name = True
    _attr_name = None  # Use device name
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
    )
    _attr_code_arm_required = False

    def __init__(self, coordinator: VerisureCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.installation.number}"
        )
        self._force_context: dict[str, Any] | None = None
        self._transitional_state: AlarmControlPanelState | None = None

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Return the current alarm state."""
        if self._transitional_state is not None:
            return self._transitional_state

        if self.coordinator.data is None:
            return None

        return _STATE_MAP.get(self.coordinator.data.alarm_state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return force-arm context as extra attributes."""
        attrs: dict[str, Any] = {}
        if self._force_context is not None:
            attrs["force_arm_available"] = True
            attrs["arm_exceptions"] = [
                e.alias for e in self._force_context["exceptions"]
            ]
        return attrs

    def _handle_coordinator_update(self) -> None:
        """Clear transitional state and force context on coordinator update."""
        self._transitional_state = None
        self._clear_force_context()
        super()._handle_coordinator_update()

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm partial + perimeter."""
        await self._async_arm(_PARTIAL_PERIMETER, "armed_home")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm total + perimeter."""
        await self._async_arm(_TOTAL_PERIMETER, "armed_away")

    async def _async_arm(self, target: AlarmState, mode: str) -> None:
        """Execute arm operation with force-arm exception handling."""
        self._transitional_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()

        try:
            await self.coordinator.client.arm(
                self.coordinator.installation, target
            )
        except ArmingExceptionError as exc:
            self._transitional_state = None
            self._set_force_context(exc, mode, target)
            self._notify_arm_exceptions(exc)
            self._fire_arming_exception_event(exc, mode)
            self.async_write_ha_state()
            return
        except (OperationFailedError, OperationTimeoutError) as exc:
            self._transitional_state = None
            _LOGGER.error("Arm failed: %s", exc.message)
            self.async_write_ha_state()
            return

        await self.coordinator.async_request_refresh()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the alarm."""
        self._transitional_state = AlarmControlPanelState.DISARMING
        self.async_write_ha_state()

        try:
            await self.coordinator.client.disarm(
                self.coordinator.installation
            )
        except (OperationFailedError, OperationTimeoutError) as exc:
            self._transitional_state = None
            _LOGGER.error("Disarm failed: %s", exc.message)
            self.async_write_ha_state()
            return

        await self.coordinator.async_request_refresh()

    # --- Force arm ---

    async def async_force_arm(self) -> None:
        """Force-arm using stored exception context.

        Called by the verisure_it.force_arm service.
        """
        if self._force_context is None:
            _LOGGER.warning("force_arm called but no force context available")
            return

        target: AlarmState = self._force_context["target"]
        ref_id: str = self._force_context["reference_id"]

        self._transitional_state = AlarmControlPanelState.ARMING
        self._clear_force_context()
        self._dismiss_notification()
        self.async_write_ha_state()

        try:
            await self.coordinator.client.arm(
                self.coordinator.installation,
                target,
                force_arming_remote_id=ref_id,
            )
        except (OperationFailedError, OperationTimeoutError) as exc:
            self._transitional_state = None
            _LOGGER.error("Force arm failed: %s", exc.message)
            self.async_write_ha_state()
            return

        await self.coordinator.async_request_refresh()

    async def async_force_arm_cancel(self) -> None:
        """Cancel pending force-arm context."""
        if self._force_context is None:
            _LOGGER.warning(
                "force_arm_cancel called but no force context available"
            )
            return

        _LOGGER.info("Force-arm cancelled by user")
        self._clear_force_context()
        self._dismiss_notification()
        self.async_write_ha_state()

    def _set_force_context(
        self,
        exc: ArmingExceptionError,
        mode: str,
        target: AlarmState,
    ) -> None:
        """Store force-arm context from an arming exception."""
        self._force_context = {
            "reference_id": exc.reference_id,
            "suid": exc.suid,
            "mode": mode,
            "target": target,
            "exceptions": exc.exceptions,
            "created_at": datetime.datetime.now(),
        }

    def _clear_force_context(self) -> None:
        """Clear stored force-arm context."""
        self._force_context = None

    def _notification_id(self) -> str:
        return f"{_NOTIFICATION_ID_PREFIX}_{self.coordinator.installation.number}"

    def _notify_arm_exceptions(self, exc: ArmingExceptionError) -> None:
        """Create a persistent notification about open zones."""
        zone_list = ", ".join(e.alias for e in exc.exceptions)
        self.hass.components.persistent_notification.async_create(
            f"Arming blocked by open zones: {zone_list}",
            title="Verisure Italy — Open Zones",
            notification_id=self._notification_id(),
        )

    def _dismiss_notification(self) -> None:
        """Dismiss the arming exception notification."""
        self.hass.components.persistent_notification.async_dismiss(
            self._notification_id()
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
```

- [ ] **Step 2: Commit**

```bash
git add custom_components/verisure_it/alarm_control_panel.py
git commit -m "feat: VerisureAlarmPanel entity — arm/disarm with force-arm flow"
```

---

### Task 8: HA Integration — Config Flow

**Files:**
- Create: `custom_components/verisure_it/config_flow.py`
- Create: `custom_components/verisure_it/strings.json`
- Create: `custom_components/verisure_it/translations/en.json`

- [ ] **Step 1: Create config_flow.py**

```python
"""Config flow for Verisure Italy."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from aiohttp import ClientSession

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from verisure_api import (
    AuthenticationError,
    Installation,
    OtpPhone,
    TwoFactorRequiredError,
    VerisureClient,
    generate_device_id,
    generate_uuid,
)

from .const import (
    CONF_DEVICE_ID,
    CONF_INSTALLATION_ALIAS,
    CONF_INSTALLATION_NUMBER,
    CONF_INSTALLATION_PANEL,
    CONF_UUID,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class VerisureItConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Verisure Italy."""

    VERSION = 1

    def __init__(self) -> None:
        self._client: VerisureClient | None = None
        self._session: ClientSession | None = None
        self._device_id: str = ""
        self._uuid: str = ""
        self._username: str = ""
        self._password: str = ""
        self._otp_hash: str = ""
        self._otp_phones: list[OtpPhone] = []
        self._installations: list[Installation] = []

    async def _get_client(self) -> VerisureClient:
        """Get or create the API client."""
        if self._client is None:
            self._device_id = generate_device_id()
            self._uuid = generate_uuid()
            self._session = ClientSession()
            self._client = VerisureClient(
                username=self._username,
                password=self._password,
                http_session=self._session,
                device_id=self._device_id,
                uuid=self._uuid,
                id_device_indigitall="",
            )
        return self._client

    async def _cleanup_session(self) -> None:
        """Close the temporary session."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Username and password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            client = await self._get_client()
            try:
                await client.login()
                return await self.async_step_installation()
            except TwoFactorRequiredError:
                # Need 2FA — get OTP challenge
                otp_hash, phones = await client.validate_device(None, None)
                if otp_hash is not None:
                    self._otp_hash = otp_hash
                    self._otp_phones = phones
                return await self.async_step_2fa()
            except AuthenticationError as err:
                _LOGGER.error("Authentication failed: %s", err.message)
                errors["base"] = "invalid_auth"
                await self._cleanup_session()
                self._client = None

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
        )

    async def async_step_2fa(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Two-factor authentication."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = await self._get_client()
            phone_id = int(user_input["phone"])
            sms_code = user_input["code"]

            # Send OTP if not yet sent
            if phone_id > 0:
                await client.send_otp(phone_id, self._otp_hash)

            try:
                await client.validate_device(self._otp_hash, sms_code)
                # Verisure IT: hash=null on validate — re-login
                await client.login()
                return await self.async_step_installation()
            except AuthenticationError as err:
                _LOGGER.error("2FA failed: %s", err.message)
                errors["base"] = "invalid_code"

        # Build phone selection
        phone_options = {
            str(p.id): p.phone for p in self._otp_phones
        }
        # If only one phone, auto-send OTP
        if len(self._otp_phones) == 1 and user_input is None:
            client = await self._get_client()
            await client.send_otp(
                self._otp_phones[0].id, self._otp_hash
            )

        return self.async_show_form(
            step_id="2fa",
            data_schema=vol.Schema({
                vol.Required("phone", default=str(self._otp_phones[0].id) if self._otp_phones else ""): vol.In(phone_options) if len(phone_options) > 1 else str,
                vol.Required("code"): str,
            }),
            errors=errors,
        )

    async def async_step_installation(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Select installation."""
        client = await self._get_client()

        if not self._installations:
            self._installations = await client.list_installations()

        # Auto-select if only one
        if len(self._installations) == 1:
            inst = self._installations[0]
            await client.get_services(inst)
            await self._cleanup_session()
            return self._create_entry(inst)

        if user_input is not None:
            number = user_input["installation"]
            inst = next(
                i for i in self._installations if i.number == number
            )
            await client.get_services(inst)
            await self._cleanup_session()
            return self._create_entry(inst)

        options = {
            i.number: f"{i.alias} ({i.address})"
            for i in self._installations
        }

        return self.async_show_form(
            step_id="installation",
            data_schema=vol.Schema({
                vol.Required("installation"): vol.In(options),
            }),
        )

    def _create_entry(self, installation: Installation) -> FlowResult:
        """Create the config entry."""
        return self.async_create_entry(
            title=installation.alias,
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_DEVICE_ID: self._device_id,
                CONF_UUID: self._uuid,
                CONF_INSTALLATION_NUMBER: installation.number,
                CONF_INSTALLATION_PANEL: installation.panel,
                CONF_INSTALLATION_ALIAS: installation.alias,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,  # noqa: ARG004
    ) -> OptionsFlow:
        """Get the options flow."""
        return VerisureItOptionsFlow()


class VerisureItOptionsFlow(OptionsFlow):
    """Options flow for Verisure Italy."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_interval = self.config_entry.options.get(
            "poll_interval", DEFAULT_POLL_INTERVAL
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(
                    "poll_interval", default=current_interval
                ): vol.All(int, vol.Range(min=3, max=300)),
            }),
        )
```

- [ ] **Step 2: Create strings.json**

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Verisure Italy Login",
        "description": "Enter your Verisure Italy credentials.",
        "data": {
          "username": "Username",
          "password": "Password"
        }
      },
      "2fa": {
        "title": "Two-Factor Authentication",
        "description": "Enter the SMS code sent to your phone.",
        "data": {
          "phone": "Phone",
          "code": "SMS Code"
        }
      },
      "installation": {
        "title": "Select Installation",
        "description": "Choose which installation to control.",
        "data": {
          "installation": "Installation"
        }
      }
    },
    "error": {
      "invalid_auth": "Invalid username or password.",
      "invalid_code": "Invalid SMS code. Please try again."
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "Verisure Italy Options",
        "data": {
          "poll_interval": "Poll interval (seconds)"
        }
      }
    }
  }
}
```

- [ ] **Step 3: Create translations/en.json**

Same content as `strings.json`:

```bash
mkdir -p custom_components/verisure_it/translations
cp custom_components/verisure_it/strings.json custom_components/verisure_it/translations/en.json
```

- [ ] **Step 4: Commit**

```bash
git add custom_components/verisure_it/config_flow.py custom_components/verisure_it/strings.json custom_components/verisure_it/translations/
git commit -m "feat: config flow — login, 2FA, installation selection, options flow"
```

---

### Task 9: HA Integration — __init__.py + Service Registration

**Files:**
- Modify: `custom_components/verisure_it/__init__.py`

- [ ] **Step 1: Replace __init__.py placeholder**

```python
"""Verisure Italy alarm integration for Home Assistant."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .coordinator import VerisureCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["alarm_control_panel"]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Set up Verisure Italy from a config entry."""
    coordinator = VerisureCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    if unload_ok:
        coordinator: VerisureCoordinator = hass.data[DOMAIN].pop(
            entry.entry_id
        )
        await coordinator.async_shutdown()

    # Remove services if no more entries
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, "force_arm")
        hass.services.async_remove(DOMAIN, "force_arm_cancel")
        hass.data.pop(DOMAIN)

    return unload_ok


def _register_services(hass: HomeAssistant) -> None:
    """Register force_arm and force_arm_cancel services."""
    if hass.services.has_service(DOMAIN, "force_arm"):
        return  # Already registered

    async def _find_entity(call: ServiceCall):
        """Find the VerisureAlarmPanel entity from a service call."""
        entity_id = call.data["entity_id"]
        # Import here to avoid circular imports
        from .alarm_control_panel import VerisureAlarmPanel

        ent_reg = er.async_get(hass)
        entry = ent_reg.async_get(entity_id)
        if entry is None:
            _LOGGER.error("Entity %s not found", entity_id)
            return None

        for coordinator in hass.data[DOMAIN].values():
            # Walk the coordinator's listeners to find our entity
            # This is a simplification — in practice, we store a reference
            pass

        # Direct approach: get entity from the platform
        component = hass.data.get("entity_components", {}).get(
            "alarm_control_panel"
        )
        if component is not None:
            entity = component.get_entity(entity_id)
            if isinstance(entity, VerisureAlarmPanel):
                return entity

        _LOGGER.error("Could not find VerisureAlarmPanel for %s", entity_id)
        return None

    async def async_force_arm(call: ServiceCall) -> None:
        """Handle force_arm service call."""
        entity = await _find_entity(call)
        if entity is not None:
            await entity.async_force_arm()

    async def async_force_arm_cancel(call: ServiceCall) -> None:
        """Handle force_arm_cancel service call."""
        entity = await _find_entity(call)
        if entity is not None:
            await entity.async_force_arm_cancel()

    service_schema = vol.Schema({
        vol.Required("entity_id"): str,
    })

    hass.services.async_register(
        DOMAIN, "force_arm", async_force_arm, schema=service_schema
    )
    hass.services.async_register(
        DOMAIN, "force_arm_cancel", async_force_arm_cancel,
        schema=service_schema,
    )
```

- [ ] **Step 2: Commit**

```bash
git add custom_components/verisure_it/__init__.py
git commit -m "feat: integration setup — entry lifecycle, force_arm services"
```

---

### Task 10: Run Full Test Suite + Lint

**Files:** (none created, validation only)

- [ ] **Step 1: Run all tests**

Run: `source .venv/bin/activate && pytest tests/ -x -q`
Expected: all tests pass (existing 88 + new state mapping + new force-arm tests)

- [ ] **Step 2: Run type checker on API client**

Run: `source .venv/bin/activate && pyright verisure_api/`
Expected: 0 errors

- [ ] **Step 3: Run linter**

Run: `source .venv/bin/activate && ruff check verisure_api/ tests/`
Expected: clean

- [ ] **Step 4: Run ruff on integration code (best-effort)**

Run: `source .venv/bin/activate && ruff check custom_components/`
Expected: clean (imports from homeassistant will be flagged as missing but that's expected — not installed in dev env)

- [ ] **Step 5: Commit any fixes**

If any issues found, fix and commit.

---

### Task 11: Deploy to Live HA Instance

**Files:** (deployment, no code changes)

- [ ] **Step 1: Copy integration to HAOS**

```bash
ssh root@homeassistant -p 22222 'rm -rf /mnt/data/supervisor/homeassistant/custom_components/verisure_it'
scp -P 22222 -r custom_components/verisure_it root@homeassistant:/mnt/data/supervisor/homeassistant/custom_components/
```

- [ ] **Step 2: Copy verisure_api to HAOS**

The integration imports `verisure_api` which needs to be available. Copy it alongside:

```bash
ssh root@homeassistant -p 22222 'rm -rf /mnt/data/supervisor/homeassistant/custom_components/verisure_it/verisure_api'
scp -P 22222 -r verisure_api root@homeassistant:/mnt/data/supervisor/homeassistant/custom_components/verisure_it/
```

Note: We need to make the integration find verisure_api. Either:
- Copy it inside the custom_component and adjust imports
- Or install it as a package on the HA venv

The simpler approach: symlink or copy into the component directory and use relative imports. This needs investigation during deployment — the import path may need adjusting.

- [ ] **Step 3: Restart HA**

```bash
ssh root@homeassistant -p 22222 'ha core restart'
```

- [ ] **Step 4: Add integration via HA UI**

Navigate to Settings > Devices & Services > Add Integration > "Verisure Italy"

- [ ] **Step 5: Verify alarm state shows correctly**

Check that the alarm control panel entity shows the current alarm state (should match what the polling script showed).

- [ ] **Step 6: Test arm/disarm from HA**

Test arm home (partial+perimeter) and disarm from the HA UI.

---

### Task 12: E2E — Force Arm with Open Zone (next day)

**Files:** (E2E testing, no code changes expected)

- [ ] **Step 1: Start polling script**

```bash
source .venv/bin/activate && python e2e_poll.py
```

- [ ] **Step 2: Open a window**

Open a window that has a magnetic sensor.

- [ ] **Step 3: Arm total+perimeter from HA**

Use the HA UI to arm away. This should trigger the NON_BLOCKING error flow.

- [ ] **Step 4: Verify exception event fires**

Check HA logs and developer tools > events for `verisure_it_arming_exception`.

- [ ] **Step 5: Verify persistent notification appears**

Check the HA notification bell for the open zone notification.

- [ ] **Step 6: Call force_arm service**

Use Developer Tools > Services > `verisure_it.force_arm` with the entity_id.

- [ ] **Step 7: Verify alarm armed**

Check both the HA entity and the polling script show the armed state.

- [ ] **Step 8: Observe xSStatus during triggered alarm**

If the alarm triggers (perimeter sensor), watch the polling script for `EXCEPTIONS` data. This informs the trigger detection future work.

- [ ] **Step 9: Disarm and close window**

Clean up.
