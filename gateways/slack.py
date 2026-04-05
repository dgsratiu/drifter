"""Slack gateway — monitors Slack channels and posts messages to the bus."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    HAS_SLACK = True
except ImportError:
    HAS_SLACK = False


def run_drifter(project_root: Path, *args: str) -> str:
    cmd = [os.environ.get("DRIFTER_BIN", "drifter"), "--db", str(project_root / "drifter.db"), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def fetch_messages(
    token: str,
    channel: str,
    oldest: float | None = None,
    limit: int = 50,
) -> list[dict]:
    """Fetch recent messages from a Slack channel."""
    if not HAS_SLACK:
        print("[slack] slack_sdk not installed, skipping", file=sys.stderr)
        return []

    try:
        client = WebClient(token=token)
        params = {"channel": channel, "limit": limit}
        if oldest is not None:
            params["oldest"] = str(oldest)

        result = client.conversations_history(**params)
        messages = result.get("messages", [])

        return [
            {
                "user": m.get("user", m.get("username", "unknown")),
                "text": m.get("text", ""),
                "ts": m.get("ts", ""),
                "thread_ts": m.get("thread_ts"),
                "reply_count": m.get("reply_count", 0),
            }
            for m in messages
            if m.get("text")
        ]
    except SlackApiError as exc:
        print(f"[slack] API error: {exc.response['error']}", file=sys.stderr)
        return []


def get_channel_list(token: str) -> list[dict]:
    """List all accessible Slack channels."""
    if not HAS_SLACK:
        return []

    try:
        client = WebClient(token=token)
        result = client.conversations_list(types="public_channel,private_channel")
        return [
            {"id": c["id"], "name": c["name"], "is_private": c.get("is_private", False)}
            for c in result.get("channels", [])
        ]
    except SlackApiError as exc:
        print(f"[slack] channel list error: {exc.response['error']}", file=sys.stderr)
        return []


def post_slack_message(project_root: Path, msg: dict, slack_channel: str, bus_channel: str = "internal") -> None:
    """Post a Slack message to the bus."""
    thread_info = ""
    if msg.get("thread_ts"):
        thread_info = f"\nThread replies: {msg['reply_count']}"

    text = msg["text"]
    if len(text) > 1000:
        text = text[:1000] + "..."

    bus_msg = (
        f"SLACK #{slack_channel} from {msg['user']}:\n{text}{thread_info}"
    )

    try:
        run_drifter(
            project_root, "post", bus_channel, bus_msg,
            "--agent", "slack-gateway",
            "--metadata", '{"trigger":"manual","source":"slack"}',
        )
    except subprocess.CalledProcessError as exc:
        print(f"[slack] failed to post: {exc.stderr}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Slack gateway — monitors Slack channels")
    parser.add_argument("--token", default=os.environ.get("SLACK_BOT_TOKEN"))
    parser.add_argument("--channel", default=os.environ.get("SLACK_CHANNEL", "general"))
    parser.add_argument("--bus-channel", default="internal", help="Bus channel to post to")
    parser.add_argument("--hours", type=int, default=1, help="Look back window in hours")
    parser.add_argument("--list-channels", action="store_true", help="List available Slack channels")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.token:
        print("[slack] SLACK_BOT_TOKEN env var required", file=sys.stderr)
        return 1

    project_root = Path(__file__).resolve().parent.parent

    if args.list_channels:
        channels = get_channel_list(args.token)
        for ch in channels:
            visibility = "private" if ch["is_private"] else "public"
            print(f"  {ch['name']} ({ch['id']}) [{visibility}]")
        return 0

    oldest = time.time() - (args.hours * 3600) if args.hours else None
    messages = fetch_messages(args.token, args.channel, oldest=oldest)

    if not messages:
        print("[slack] no recent messages")
        return 0

    print(f"[slack] found {len(messages)} message(s) in #{args.channel}")

    if args.dry_run:
        for m in messages:
            print(f"  {m['user']}: {m['text'][:80]}")
        return 0

    for m in messages:
        post_slack_message(project_root, m, args.channel, args.bus_channel)

    print(f"[slack] posted {len(messages)} message(s) to #{args.bus_channel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
