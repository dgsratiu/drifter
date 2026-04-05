"""Twitch gateway — fetches live stream events and chat activity, posts to the bus."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None


def run_drifter(project_root: Path, *args: str) -> str:
    cmd = [os.environ.get("DRIFTER_BIN", "drifter"), "--db", str(project_root / "drifter.db"), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def get_app_token(client_id: str, client_secret: str) -> str | None:
    """Get an app access token via Client Credentials flow."""
    if requests is None:
        print("[twitch] requests not installed, skipping", file=sys.stderr)
        return None

    resp = requests.post(
        "https://id.twitch.tv/oauth2/token",
        params={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
    )
    if resp.status_code == 200:
        return resp.json().get("access_token")
    print(f"[twitch] token request failed: {resp.status_code} {resp.text}", file=sys.stderr)
    return None


def get_user_id(client_id: str, token: str, login: str) -> str | None:
    """Get a user ID from their login name."""
    if requests is None:
        return None

    resp = requests.get(
        f"https://api.twitch.tv/helix/users?login={login}",
        headers={"Client-ID": client_id, "Authorization": f"Bearer {token}"},
    )
    if resp.status_code == 200:
        data = resp.json().get("data", [])
        return data[0]["id"] if data else None
    return None


def check_stream_status(client_id: str, token: str, user_id: str) -> dict | None:
    """Check if a channel is currently live."""
    if requests is None:
        return None

    resp = requests.get(
        f"https://api.twitch.tv/helix/streams?user_id={user_id}",
        headers={"Client-ID": client_id, "Authorization": f"Bearer {token}"},
    )
    if resp.status_code == 200:
        data = resp.json().get("data", [])
        return data[0] if data else None
    return None


def post_stream_event(project_root: Path, stream: dict, channel: str, went_live: bool) -> None:
    """Post a stream status change to the bus."""
    status = "LIVE" if went_live else "OFFLINE"
    game = stream.get("game_name", "Unknown")
    title = stream.get("title", "No title")
    viewers = stream.get("viewer_count", 0)

    msg = (
        f"TWITCH STREAM {status}: {title}\n"
        f"Game: {game}\n"
        f"Viewers: {viewers}\n"
        f"Started: {stream.get('started_at', 'unknown')}"
    )

    try:
        run_drifter(
            project_root, "post", channel, msg,
            "--agent", "twitch-gateway",
            "--metadata", '{"trigger":"manual","source":"twitch"}',
        )
    except subprocess.CalledProcessError as exc:
        print(f"[twitch] failed to post: {exc.stderr}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Twitch gateway — monitors stream status")
    parser.add_argument("--channel-login", default=os.environ.get("TWITCH_CHANNEL", ""))
    parser.add_argument("--client-id", default=os.environ.get("TWITCH_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.environ.get("TWITCH_CLIENT_SECRET"))
    parser.add_argument("--bus-channel", default="internal", help="Bus channel to post to")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.channel_login:
        print("[twitch] TWITCH_CHANNEL env var required", file=sys.stderr)
        return 1
    if not args.client_id or not args.client_secret:
        print("[twitch] TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET env vars required", file=sys.stderr)
        return 1

    project_root = Path(__file__).resolve().parent.parent

    token = get_app_token(args.client_id, args.client_secret)
    if not token:
        return 1

    user_id = get_user_id(args.client_id, token, args.channel_login)
    if not user_id:
        print(f"[twitch] could not find user ID for {args.channel_login}", file=sys.stderr)
        return 1

    stream = check_stream_status(args.client_id, token, user_id)

    if stream:
        print(f"[twitch] {args.channel_login} is LIVE: {stream.get('title', '')}")
        if not args.dry_run:
            post_stream_event(project_root, stream, args.bus_channel, went_live=True)
    else:
        print(f"[twitch] {args.channel_login} is offline")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
