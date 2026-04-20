"""Command-line interface for the Verisure Italy client.

Offers `login`, `probe`, and `logout` subcommands. Shares the library
code with the HA integration — the probe output is byte-identical
whether captured from HA logs or this CLI. The CLI exists for fast
iteration when diagnosing unsupported panels without requiring users
to upgrade/restart HA.

**Read-only by design.** No arm/disarm subcommands. Sending a wrong
command to a panel is dangerous; the HA UI is the authorized path.

Session is persisted at XDG_CACHE_HOME/verisure-italy/session.json
with mode 0600. Only refresh_token + device identifiers are stored.
Password is never written to disk.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import ClientSession
from pydantic import BaseModel

from . import __version__
from .client import VerisureClient, generate_device_id, generate_uuid
from .exceptions import AuthenticationError, TwoFactorRequiredError, VerisureError
from .probe import run_probe

if TYPE_CHECKING:
    from .models import Installation

_LOGGER = logging.getLogger("verisure_italy.cli")


def _session_path() -> Path:
    """Resolve session cache path, XDG_CACHE_HOME aware."""
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    return base / "verisure-italy" / "session.json"


class CachedSession(BaseModel):
    """Persisted CLI session — everything needed to avoid re-prompting.

    Stored as JSON at XDG_CACHE_HOME/verisure-italy/session.json (0600).
    Password is deliberately absent; users re-enter it for each `probe`.
    """

    model_config = {"frozen": True}

    username: str
    refresh_token: str
    device_id: str
    uuid: str
    id_device_indigitall: str
    saved_at: str


def _save_session(session: CachedSession) -> Path:
    """Write session to disk with 0600 perms. Returns the file path."""
    path = _session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session.model_dump_json(indent=2))
    path.chmod(0o600)
    return path


def _load_session() -> CachedSession:
    """Read session from disk. Raises FileNotFoundError if none cached."""
    path = _session_path()
    if not path.exists():
        raise FileNotFoundError(
            f"No cached session at {path}. Run `verisure-italy-cli login` first."
        )
    mode = path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    if mode != 0:
        _LOGGER.warning(
            "Session file %s has broad permissions (0%o) — should be 0600.",
            path, path.stat().st_mode & 0o777,
        )
    return CachedSession.model_validate_json(path.read_text())


async def _login_flow(username: str, password: str) -> CachedSession:
    """Run the interactive login flow and return a persisted session."""
    device_id = generate_device_id()
    uuid = generate_uuid()
    id_device_indigitall = generate_uuid()

    async with ClientSession() as http:
        client = VerisureClient(
            username=username,
            password=password,
            http_session=http,
            device_id=device_id,
            uuid=uuid,
            id_device_indigitall=id_device_indigitall,
        )
        try:
            await client.login()
        except TwoFactorRequiredError:
            await _handle_two_factor(client)
            await client.login()

        if not client.refresh_token:
            raise VerisureError(
                "Login succeeded but no refresh token returned; cannot cache session."
            )

        return CachedSession(
            username=username,
            refresh_token=client.refresh_token,
            device_id=device_id,
            uuid=uuid,
            id_device_indigitall=id_device_indigitall,
            saved_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )


async def _handle_two_factor(client: VerisureClient) -> None:
    """Walk the user through the device-validation + OTP challenge."""
    sys.stderr.write("Two-factor authentication required.\n")
    otp_hash, phones = await client.validate_device(None, None)
    if otp_hash is None:
        sys.stderr.write("Device validated.\n")
        return

    if not phones:
        raise AuthenticationError("OTP challenge returned no phone numbers.")

    sys.stderr.write("Available phones:\n")
    for phone in phones:
        sys.stderr.write(f"  [{phone.id}] {phone.phone}\n")
    choice = input("Phone id to receive SMS: ").strip()
    phone_id = int(choice)

    sent = await client.send_otp(phone_id, otp_hash)
    if not sent:
        raise AuthenticationError("send_otp() rejected by API.")

    code = input("SMS code: ").strip()
    await client.validate_device(otp_hash, code)
    sys.stderr.write("OTP validated.\n")


async def _build_authenticated_client(
    session: CachedSession, http: ClientSession, password: str,
) -> VerisureClient:
    """Rehydrate a VerisureClient from cached session + fresh password.

    Verisure IT's refresh-token flow is not documented in our client, so
    we re-login with the cached device identifiers. This keeps the
    device registered (no 2FA re-prompt for a known device).
    """
    client = VerisureClient(
        username=session.username,
        password=password,
        http_session=http,
        device_id=session.device_id,
        uuid=session.uuid,
        id_device_indigitall=session.id_device_indigitall,
    )
    try:
        await client.login()
    except TwoFactorRequiredError:
        await _handle_two_factor(client)
        await client.login()
    return client


async def _select_installation(
    client: VerisureClient, preferred_index: int | None,
) -> Installation:
    """Return the chosen installation (preferred_index is 0-based)."""
    installations = await client.list_installations()
    if not installations:
        raise VerisureError("No installations found on this account.")
    if preferred_index is None:
        if len(installations) > 1:
            sys.stderr.write(
                f"Found {len(installations)} installations; using first. "
                f"Pass --installation N to choose another.\n"
            )
        return installations[0]
    if not 0 <= preferred_index < len(installations):
        raise VerisureError(
            f"--installation {preferred_index} out of range "
            f"(have {len(installations)})."
        )
    return installations[preferred_index]


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


async def cmd_login(args: argparse.Namespace) -> int:
    """Prompt for credentials, run login, persist session.

    Honours VERISURE_USERNAME and VERISURE_PASSWORD env vars for
    non-interactive use (CI, e2e smoke).
    """
    username = (
        args.username
        or os.environ.get("VERISURE_USERNAME")
        or input("Verisure email: ").strip()
    )
    password = os.environ.get("VERISURE_PASSWORD") or getpass.getpass("Password: ")
    session = await _login_flow(username, password)
    path = _save_session(session)
    sys.stderr.write(f"Session saved to {path}\n")
    return 0


async def cmd_probe(args: argparse.Namespace) -> int:
    """Load cached session, run probe, print redacted JSON to stdout."""
    session = _load_session()
    password = (
        os.environ.get("VERISURE_PASSWORD")
        or getpass.getpass(f"Password for {session.username}: ")
    )
    async with ClientSession() as http:
        client = await _build_authenticated_client(session, http, password)
        installation = await _select_installation(client, args.installation)
        probe = await run_probe(client, installation)

    indent = 2 if args.pretty else None
    sys.stdout.write(json.dumps(probe, indent=indent, sort_keys=args.pretty))
    sys.stdout.write("\n")
    return 0


async def cmd_logout(args: argparse.Namespace) -> int:
    _ = args
    """Delete cached session file, if any."""
    path = _session_path()
    if path.exists():
        path.unlink()
        sys.stderr.write(f"Deleted {path}\n")
    else:
        sys.stderr.write("No cached session to delete.\n")
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argparse parser."""
    parser = argparse.ArgumentParser(
        prog="verisure-italy-cli",
        description=(
            "Read-only diagnostic CLI for the Verisure Italy API. "
            "Login once, run `probe` to dump panel capabilities as "
            "redacted JSON."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="Enable debug logging (-v for INFO, -vv for DEBUG).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_login = subparsers.add_parser("login", help="Authenticate and cache session.")
    p_login.add_argument(
        "--username", help="Verisure account email (prompted if omitted).",
    )
    p_login.set_defaults(func=cmd_login)

    p_probe = subparsers.add_parser(
        "probe",
        help="Dump panel capabilities as redacted JSON (read-only).",
    )
    p_probe.add_argument(
        "--installation", type=int, default=None,
        help="0-based index of installation to probe (default: first).",
    )
    p_probe.add_argument(
        "--pretty", action="store_true",
        help="Pretty-print JSON output.",
    )
    p_probe.set_defaults(func=cmd_probe)

    p_logout = subparsers.add_parser("logout", help="Delete cached session.")
    p_logout.set_defaults(func=cmd_logout)

    return parser


def _configure_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return asyncio.run(args.func(args))
    except KeyboardInterrupt:
        sys.stderr.write("\nAborted.\n")
        return 130
    except VerisureError as err:
        sys.stderr.write(f"Error: {err.message}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
