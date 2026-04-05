"""SMS gateway — receives SMS via webhook and posts to the bus.

Supports Twilio as the SMS provider.
Run as a standalone webhook server or called by cron to poll for new messages.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from flask import Flask, request
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

try:
    from twilio.rest import Client
    HAS_TWILIO = True
except ImportError:
    HAS_TWILIO = False


def run_drifter(project_root: Path, *args: str) -> str:
    cmd = [os.environ.get("DRIFTER_BIN", "drifter"), "--db", str(project_root / "drifter.db"), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def post_sms(project_root: Path, from_number: str, body: str, channel: str = "internal") -> None:
    """Post an SMS to the bus."""
    msg = (
        f"SMS from {from_number}:\n{body}"
    )

    try:
        run_drifter(
            project_root, "post", channel, msg,
            "--agent", "sms-gateway",
            "--metadata", '{"trigger":"manual","source":"sms"}',
        )
    except subprocess.CalledProcessError as exc:
        print(f"[sms] failed to post: {exc.stderr}", file=sys.stderr)


def fetch_recent_sms(account_sid: str, auth_token: str, minutes: int = 10) -> list[dict]:
    """Fetch recent SMS messages via Twilio API."""
    if not HAS_TWILIO:
        print("[sms] twilio not installed, skipping", file=sys.stderr)
        return []

    client = Client(account_sid, auth_token)
    cutoff = datetime.now(timezone.utc).replace(microsecond=0)
    cutoff_str = cutoff.isoformat()

    messages = client.messages.list(date_sent_after=cutoff_str, limit=50)
    return [
        {
            "from": m.from_,
            "body": m.body or "",
            "date_sent": str(m.date_sent),
            "sid": m.sid,
        }
        for m in messages
        if m.body
    ]


def run_webhook(port: int, project_root: Path, channel: str) -> None:
    """Run a Flask webhook that receives SMS from Twilio."""
    if not HAS_FLASK:
        print("[sms] flask not installed, cannot run webhook", file=sys.stderr)
        sys.exit(1)

    app = Flask(__name__)

    @app.route("/sms", methods=["POST"])
    def receive_sms():
        from_number = request.form.get("From", "unknown")
        body = request.form.get("Body", "")

        if body:
            post_sms(project_root, from_number, body, channel)

        # Return empty TwiML
        return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>', 200

    print(f"[sms] webhook listening on port {port}")
    app.run(host="0.0.0.0", port=port)


def main() -> int:
    parser = argparse.ArgumentParser(description="SMS gateway — posts SMS to the bus")
    parser.add_argument("--mode", choices=["webhook", "poll"], default="webhook")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--channel", default="internal")
    parser.add_argument("--minutes", type=int, default=10, help="Poll window in minutes")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent

    if args.mode == "webhook":
        run_webhook(args.port, project_root, args.channel)
        return 0

    # Poll mode
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")

    if not account_sid or not auth_token:
        print("[sms] TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN env vars required", file=sys.stderr)
        return 1

    messages = fetch_recent_sms(account_sid, auth_token, args.minutes)

    if not messages:
        print("[sms] no recent messages")
        return 0

    print(f"[sms] found {len(messages)} recent message(s)")

    if args.dry_run:
        for m in messages:
            print(f"  - {m['from']}: {m['body'][:80]}")
        return 0

    for m in messages:
        post_sms(project_root, m["from"], m["body"], args.channel)

    print(f"[sms] posted {len(messages)} message(s) to #{args.channel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
