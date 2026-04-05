"""Calendar gateway — fetches upcoming events from Google Calendar and posts to the bus."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def run_drifter(project_root: Path, *args: str) -> str:
    """Run a drifter CLI command and return stdout."""
    cmd = [os.environ.get("DRIFTER_BIN", "drifter"), "--db", str(project_root / "drifter.db"), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def fetch_events(calendar_id: str, minutes_ahead: int = 60, credentials_path: str | None = None) -> list[dict]:
    """Fetch upcoming events from Google Calendar.

    Requires google-api-python-client and oauth2client.
    If credentials_path is not provided, uses GOOGLE_APPLICATION_CREDENTIALS env var.
    """
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        print("[calendar] google-api-python-client not installed, skipping", file=sys.stderr)
        return []

    cred_path = credentials_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not cred_path or not Path(cred_path).exists():
        print(f"[calendar] no credentials found at {cred_path}, skipping", file=sys.stderr)
        return []

    try:
        creds = Credentials.from_authorized_user_file(cred_path, ["https://www.googleapis.com/auth/calendar.readonly"])
        service = build("calendar", "v3", credentials=creds)

        now = datetime.now(timezone.utc)
        later = now + timedelta(minutes=minutes_ahead)

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=now.isoformat(),
            timeMax=later.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = events_result.get("items", [])
        return [
            {
                "summary": e.get("summary", "(no title)"),
                "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
                "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
                "attendees": [a.get("email", "") for a in e.get("attendees", []) if a.get("email")],
                "description": e.get("description", ""),
                "location": e.get("location", ""),
                "hangout_link": e.get("hangoutLink", e.get("conferenceData", {}).get("entryPoints", [{}])[0].get("uri", "")),
            }
            for e in events
        ]
    except HttpError as exc:
        print(f"[calendar] Google Calendar API error: {exc}", file=sys.stderr)
        return []


def post_events(project_root: Path, events: list[dict], channel: str = "meetings") -> int:
    """Post calendar events to the bus. Returns count posted."""
    posted = 0
    for event in events:
        attendees = ", ".join(event["attendees"]) if event["attendees"] else "none"
        location = f"\nLocation: {event['location']}" if event["location"] else ""
        description = f"\n{event['description']}" if event["description"] else ""
        hangout = f"\nJoin: {event['hangout_link']}" if event["hangout_link"] else ""

        msg = (
            f"CALENDAR EVENT: {event['summary']}\n"
            f"Start: {event['start']}\n"
            f"End: {event['end']}\n"
            f"Attendees: {attendees}{location}{description}{hangout}"
        )

        try:
            run_drifter(
                project_root, "post", channel, msg,
                "--agent", "calendar-gateway",
                "--metadata", '{"trigger":"manual","source":"calendar"}',
            )
            posted += 1
        except subprocess.CalledProcessError as exc:
            print(f"[calendar] failed to post event: {exc.stderr}", file=sys.stderr)

    return posted


def main() -> int:
    parser = argparse.ArgumentParser(description="Calendar gateway — posts upcoming events to the bus")
    parser.add_argument("--calendar-id", default="primary", help="Google Calendar ID (default: primary)")
    parser.add_argument("--minutes-ahead", type=int, default=60, help="How far ahead to look (default: 60)")
    parser.add_argument("--credentials", default=None, help="Path to Google credentials file")
    parser.add_argument("--channel", default="meetings", help="Bus channel to post to (default: meetings)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't post")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    events = fetch_events(args.calendar_id, args.minutes_ahead, args.credentials)

    if not events:
        print("[calendar] no upcoming events")
        return 0

    print(f"[calendar] found {len(events)} upcoming event(s)")

    if args.dry_run:
        for event in events:
            print(f"  - {event['summary']} ({event['start']})")
        return 0

    posted = post_events(project_root, events, args.channel)
    print(f"[calendar] posted {posted} event(s) to #{args.channel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
