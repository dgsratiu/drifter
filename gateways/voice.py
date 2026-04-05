"""Voice gateway — processes voice call transcripts and posts to the bus.

Accepts audio files or transcript text from voice providers (Twilio, etc.)
and posts structured summaries to the bus.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def run_drifter(project_root: Path, *args: str) -> str:
    cmd = [os.environ.get("DRIFTER_BIN", "drifter"), "--db", str(project_root / "drifter.db"), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def post_transcript(
    project_root: Path,
    caller: str,
    duration: str,
    transcript: str,
    channel: str = "meetings",
) -> None:
    """Post a voice transcript to the bus."""
    summary = transcript[:1000] if len(transcript) > 1000 else transcript

    msg = (
        f"VOICE CALL from {caller}\n"
        f"Duration: {duration}\n\n"
        f"Transcript:\n{summary}"
    )

    try:
        run_drifter(
            project_root, "post", channel, msg,
            "--agent", "voice-gateway",
            "--metadata", '{"trigger":"manual","source":"voice"}',
        )
    except subprocess.CalledProcessError as exc:
        print(f"[voice] failed to post: {exc.stderr}", file=sys.stderr)


def transcribe_audio(audio_path: str) -> str | None:
    """Transcribe an audio file using OpenAI Whisper API."""
    try:
        from openai import OpenAI
    except ImportError:
        print("[voice] openai not installed, skipping transcription", file=sys.stderr)
        return None

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[voice] OPENAI_API_KEY env var required", file=sys.stderr)
        return None

    try:
        client = OpenAI(api_key=api_key)
        with open(audio_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
            )
        return response.text
    except Exception as exc:
        print(f"[voice] transcription error: {exc}", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Voice gateway — posts call transcripts to the bus")
    parser.add_argument("--caller", default="unknown")
    parser.add_argument("--duration", default="unknown")
    parser.add_argument("--transcript", default=None, help="Transcript text (or provide --audio)")
    parser.add_argument("--audio", default=None, help="Path to audio file to transcribe")
    parser.add_argument("--channel", default="meetings")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent

    transcript = args.transcript
    if args.audio:
        transcript = transcribe_audio(args.audio)
        if not transcript:
            return 1

    if not transcript:
        print("[voice] no transcript provided", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[voice] would post from {args.caller} ({args.duration}):")
        print(transcript[:200])
        return 0

    post_transcript(project_root, args.caller, args.duration, transcript, args.channel)
    print(f"[voice] posted transcript to #{args.channel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
