"""CLI tests.

Cover argparse wiring, session round-trip with 0600 perms, XDG_CACHE_HOME
resolution. Network flows (login, probe) are NOT tested here — they're
covered indirectly via test_probe.py with real HTTP mocks. CLI tests
focus on the plumbing that only the CLI owns.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from verisure_italy.cli import (
    CachedSession,
    _load_session,
    _save_session,
    _session_path,
    build_parser,
)


class TestSessionPath:
    def test_uses_xdg_cache_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert _session_path() == tmp_path / "verisure-italy" / "session.json"

    def test_falls_back_to_home_cache(self, monkeypatch, tmp_path):
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        # Path.home() reads HOME on POSIX
        assert _session_path() == tmp_path / ".cache" / "verisure-italy" / "session.json"


class TestSessionRoundTrip:
    @pytest.fixture
    def session(self):
        return CachedSession(
            username="u@e.it",
            refresh_token="refresh-xyz",
            device_id="dev-abc",
            uuid="uuid-123",
            id_device_indigitall="ind-999",
            saved_at="2026-04-20T12:00:00Z",
        )

    def test_save_then_load_identity(self, session, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        path = _save_session(session)
        assert path.exists()
        loaded = _load_session()
        assert loaded == session

    def test_save_sets_0600(self, session, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        path = _save_session(session)
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_save_creates_parent_dir(self, session, monkeypatch, tmp_path):
        nested = tmp_path / "nowhere"
        monkeypatch.setenv("XDG_CACHE_HOME", str(nested))
        path = _save_session(session)
        assert path.parent.is_dir()
        assert path.exists()

    def test_load_missing_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        with pytest.raises(FileNotFoundError):
            _load_session()

    def test_load_warns_on_broad_perms(self, session, monkeypatch, tmp_path, caplog):
        import logging
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        path = _save_session(session)
        path.chmod(0o644)  # world-readable
        with caplog.at_level(logging.WARNING, logger="verisure_italy.cli"):
            _load_session()
        assert any("broad permissions" in r.message for r in caplog.records)

    def test_roundtrip_preserves_all_fields(
        self, session, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        path = _save_session(session)
        payload = json.loads(path.read_text())
        assert payload == {
            "username": "u@e.it",
            "refresh_token": "refresh-xyz",
            "device_id": "dev-abc",
            "uuid": "uuid-123",
            "id_device_indigitall": "ind-999",
            "saved_at": "2026-04-20T12:00:00Z",
        }


class TestArgParsing:
    def test_login_command(self):
        parser = build_parser()
        args = parser.parse_args(["login"])
        assert args.command == "login"

    def test_login_with_username(self):
        parser = build_parser()
        args = parser.parse_args(["login", "--username", "u@e.it"])
        assert args.username == "u@e.it"

    def test_probe_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["probe"])
        assert args.command == "probe"
        assert args.installation is None
        assert args.pretty is False

    def test_probe_with_installation_and_pretty(self):
        parser = build_parser()
        args = parser.parse_args(["probe", "--installation", "2", "--pretty"])
        assert args.installation == 2
        assert args.pretty is True

    def test_logout(self):
        parser = build_parser()
        args = parser.parse_args(["logout"])
        assert args.command == "logout"

    def test_verbose_counter(self):
        parser = build_parser()
        args = parser.parse_args(["-vv", "probe"])
        assert args.verbose == 2

    def test_no_command_fails(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])


class TestLogoutDeletesFile:
    """Integration-ish: verify that running logout removes the session file."""

    def test_logout_removes_file(self, monkeypatch, tmp_path):
        import asyncio

        from verisure_italy.cli import cmd_logout

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        session = CachedSession(
            username="u@e.it",
            refresh_token="r",
            device_id="d",
            uuid="u",
            id_device_indigitall="i",
            saved_at="2026-04-20T00:00:00Z",
        )
        _save_session(session)
        assert _session_path().exists()

        parser = build_parser()
        args = parser.parse_args(["logout"])
        rc = asyncio.run(cmd_logout(args))
        assert rc == 0
        assert not _session_path().exists()

    def test_logout_idempotent(self, monkeypatch, tmp_path):
        import asyncio

        from verisure_italy.cli import cmd_logout

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        parser = build_parser()
        args = parser.parse_args(["logout"])
        rc = asyncio.run(cmd_logout(args))
        assert rc == 0


class TestPermissionConstantsSanity:
    """Guard against typos in the permission checks."""

    def test_0o600_strips_group_and_other(self):
        mode = 0o600
        group_other = mode & (stat.S_IRWXG | stat.S_IRWXO)
        assert group_other == 0

    def test_0o644_is_caught_as_broad(self):
        mode = 0o644
        group_other = mode & (stat.S_IRWXG | stat.S_IRWXO)
        assert group_other != 0


class TestShellWrapperExists:
    """The bare-clone wrapper must exist, be executable, invoke the CLI."""

    def _wrapper(self) -> Path:
        here = Path(__file__).resolve().parent.parent
        return here / "scripts" / "verisure-italy-cli"

    def test_exists(self):
        assert self._wrapper().exists()

    def test_is_executable(self):
        mode = self._wrapper().stat().st_mode
        assert mode & stat.S_IXUSR

    def test_invokes_cli_module(self):
        content = self._wrapper().read_text()
        assert "verisure_italy.cli" in content
