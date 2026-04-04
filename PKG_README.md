# verisure-italy

Async Python client for the **Verisure Italy** alarm API.

Talks directly to `customers.verisure.it/owa-api/graphql`. Typed,
async, Pydantic models for all request/response types. No `Any`,
no dict soup. Pyright strict, zero errors.

> **Not affiliated with Verisure Group or Securitas Direct.**

## Installation

```bash
pip install verisure-italy
```

## Usage

```python
import aiohttp
from verisure_italy import VerisureClient, generate_device_id, generate_uuid

async with aiohttp.ClientSession() as session:
    client = VerisureClient(
        username="your@email.com",
        password="your-password",
        http_session=session,
        device_id=generate_device_id(),
        uuid=generate_uuid(),
        id_device_indigitall="",
    )

    # Login (may raise TwoFactorRequiredError)
    await client.login()

    # List installations
    installations = await client.list_installations()
    inst = installations[0]
    await client.get_services(inst)

    # Get alarm status (passive — no panel ping, no timeline entry)
    status = await client.get_general_status(inst)
    print(f"Alarm: {status.status}")  # D, B, A, E, P, T

    # Arm / disarm
    from verisure_italy import AlarmState, InteriorMode, PerimeterMode

    target = AlarmState(interior=InteriorMode.PARTIAL, perimeter=PerimeterMode.ON)
    result = await client.arm(inst, target)

    # Force-arm (bypassing open zones — admin users only)
    from verisure_italy.exceptions import ArmingExceptionError

    try:
        await client.arm(inst, target)
    except ArmingExceptionError as exc:
        print(f"Open zones: {[e.alias for e in exc.exceptions]}")
        await client.arm(
            inst, target,
            force_arming_remote_id=exc.reference_id,
            suid=exc.suid,
        )

    await client.disarm(inst)
    await client.logout()
```

## Alarm States

| State | Interior | Perimeter | Proto |
|---|---|---|---|
| Disarmed | OFF | OFF | `D` |
| Partial + Perimeter | PARTIAL | ON | `B` |
| Total + Perimeter | TOTAL | ON | `A` |
| Perimeter only | OFF | ON | `E` |
| Partial | PARTIAL | OFF | `P` |
| Total | TOTAL | OFF | `T` |

## Home Assistant

Looking for the Home Assistant integration? See
[ha-verisure-italy](https://github.com/vjt/ha-verisure-italy) on GitHub,
or install via HACS.

## License

MIT. See [LICENSE](https://github.com/vjt/ha-verisure-italy/blob/master/LICENSE).
