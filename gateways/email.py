"""Email gateway — fetches unread emails and posts summaries to the bus."""

from __future__ import annotations

import argparse
import email as email_mod
import email.header
import imaplib
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def run_drifter(project_root: Path, *args: str) -> str:
    """Run a drifter CLI command and return stdout."""
    cmd = [os.environ.get("DRIFTER_BIN", "drifter"), "--db", str(project_root / "drifter.db"), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def decode_header_value(value: str) -> str:
    """Decode RFC 2047 encoded header values."""
    parts = email_mod.header.decode_header(value)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded)


def fetch_emails(
    imap_host: str,
    imap_user: str,
    imap_pass: str,
    mailbox: str = "INBOX",
    unread_only: bool = True,
    max_count: int = 20,
) -> list[dict]:
    """Fetch emails via IMAP. Returns list of parsed email dicts."""
    emails = []
    try:
        mail = imaplib.IMAP4_SSL(imap_host)
        mail.login(imap_user, imap_pass)
        mail.select(mailbox)

        search_criteria = "UNSEEN" if unread_only else "ALL"
        status, data = mail.search(None, search_criteria)
        if status != "OK":
            print(f"[email] IMAP search failed: {status}", file=sys.stderr)
            return []

        msg_ids = data[0].split()
        msg_ids = msg_ids[-max_count:]  # Take most recent

        for msg_id in msg_ids:
            status, msg_data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue

            raw = msg_data[0][1]
            msg = email_mod.message_from_bytes(raw)

            subject = decode_header_value(msg.get("Subject", "(no subject)"))
            from_addr = decode_header_value(msg.get("From", "unknown"))
            to_addr = decode_header_value(msg.get("To", ""))
            date_str = msg.get("Date", "")

            # Extract plain text body
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    if content_type == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or "utf-8"
                            body = payload.decode(charset, errors="replace")
                            break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")

            # Truncate long bodies
            if len(body) > 2000:
                body = body[:2000] + "\n...(truncated)"

            emails.append({
                "subject": subject,
                "from": from_addr,
                "to": to_addr,
                "date": date_str,
                "body": body.strip(),
            })

        mail.logout()
    except imaplib.IMAP4.error as exc:
        print(f"[email] IMAP error: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"[email] unexpected error: {exc}", file=sys.stderr)

    return emails


def post_emails(project_root: Path, emails: list[dict], channel: str = "internal") -> int:
    """Post emails to the bus. Returns count posted."""
    posted = 0
    for em in emails:
        body_preview = em["body"][:500] if em["body"] else "(empty)"

        msg = (
            f"EMAIL: {em['subject']}\n"
            f"From: {em['from']}\n"
            f"To: {em['to']}\n"
            f"Date: {em['date']}\n\n"
            f"{body_preview}"
        )

        try:
            run_drifter(
                project_root, "post", channel, msg,
                "--agent", "email-gateway",
                "--metadata", '{"trigger":"manual","source":"email"}',
            )
            posted += 1
        except subprocess.CalledProcessError as exc:
            print(f"[email] failed to post: {exc.stderr}", file=sys.stderr)

    return posted


def main() -> int:
    parser = argparse.ArgumentParser(description="Email gateway — posts unread emails to the bus")
    parser.add_argument("--host", default=os.environ.get("IMAP_HOST", "imap.gmail.com"))
    parser.add_argument("--user", default=os.environ.get("IMAP_USER"))
    parser.add_argument("--password", default=os.environ.get("IMAP_PASSWORD"))
    parser.add_argument("--mailbox", default="INBOX")
    parser.add_argument("--all", action="store_true", help="Fetch all emails, not just unread")
    parser.add_argument("--max-count", type=int, default=20, help="Max emails to fetch")
    parser.add_argument("--channel", default="internal", help="Bus channel to post to")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't post")
    args = parser.parse_args()

    if not args.user or not args.password:
        print("[email] IMAP_USER and IMAP_PASSWORD env vars required", file=sys.stderr)
        return 1

    project_root = Path(__file__).resolve().parent.parent
    emails_list = fetch_emails(
        args.host, args.user, args.password,
        args.mailbox, unread_only=not args.all,
        max_count=args.max_count,
    )

    if not emails_list:
        print("[email] no emails found")
        return 0

    print(f"[email] found {len(emails_list)} email(s)")

    if args.dry_run:
        for em in emails_list:
            print(f"  - {em['subject']} (from: {em['from']})")
        return 0

    posted = post_emails(project_root, emails_list, args.channel)
    print(f"[email] posted {posted} email(s) to #{args.channel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
