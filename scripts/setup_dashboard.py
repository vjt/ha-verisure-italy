#!/usr/bin/env python3
"""Create the Verisure Italy dashboard on Home Assistant.

One-time setup script. After creation, the integration auto-manages
the dashboard config on every reload.

Usage:
    python scripts/setup_dashboard.py --url http://homeassistant:8123 --token YOUR_LLAT

The token is a Long-Lived Access Token from your HA profile page.
You can also set HA_URL and HA_TOKEN environment variables.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import aiohttp


async def main(url: str, token: str) -> int:
    ws_url = url.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_url}/api/websocket"

    async with aiohttp.ClientSession() as session, session.ws_connect(ws_url) as ws:
        # Auth handshake
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": token})
        msg = await ws.receive_json()
        if msg["type"] != "auth_ok":
            print(f"Authentication failed: {msg}", file=sys.stderr)
            return 1

        # Check if dashboard already exists
        await ws.send_json({"id": 1, "type": "lovelace/dashboards/list"})
        result = await ws.receive_json()
        for d in result.get("result", []):
            if d.get("url_path") == "verisure-italy":
                print("Dashboard 'verisure-italy' already exists.")
                print("Reload the integration to populate it.")
                return 0

        # Create dashboard
        await ws.send_json({
            "id": 2,
            "type": "lovelace/dashboards/create",
            "url_path": "verisure-italy",
            "title": "Verisure",
            "icon": "mdi:shield-home",
            "mode": "storage",
            "require_admin": False,
        })
        result = await ws.receive_json()
        if not result.get("success"):
            error = result.get("error", {}).get("message", "unknown")
            print(f"Failed to create dashboard: {error}", file=sys.stderr)
            return 1

        print("Dashboard 'verisure-italy' created.")
        print("Reload the Verisure Italy integration to populate it.")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("HA_URL", "http://homeassistant:8123"),
        help="Home Assistant URL (default: $HA_URL or http://homeassistant:8123)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("HA_TOKEN"),
        help="Long-Lived Access Token (default: $HA_TOKEN)",
    )
    args = parser.parse_args()

    if not args.token:
        parser.error("--token or HA_TOKEN environment variable required")

    sys.exit(asyncio.run(main(args.url, args.token)))
