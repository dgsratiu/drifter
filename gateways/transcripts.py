"""Transcripts gateway — watches a directory for .md transcript files and posts to the bus.

Scans a directory for .md files, reads their content, and posts each as a
meeting transcript to the #meetings channel. Tracks posted files via a
.state file to avoid duplicates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def run_drifter(project_root: Path, *args: str) -> str:
    """Run a drifter CLI command and return stdout."""
    cmd = [os.environ.get("DRIFTER_BIN", "drifter"), "--db", str(project_root / "drifter.db"), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def load_state(state_path: Path) -> set[str]:
    """Load set of already-posted file hashes."""
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text())
            return set(data.get("posted", []))
        except (json.JSONDecodeError, KeyError):
            return set()
    return set()


def save_state(state_path: Path, posted: set[str]) -> None:
    """Save set of posted file hashes."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"posted": sorted(posted)}))


def file_hash(path: Path) -> str:
    """Return SHA-256 hash of file content."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def post_transcript(
    project_root: Path,
    filename: str,
    content: str,
    channel: str,
) -> None:
    """Post a meeting transcript to the bus."""
    summary = content[:2000] if len(content) > 2000 else content

    msg = f"MEETING TRANSCRIPT: {filename}\n\n{summary}"

    try:
        run_drifter(
            project_root, "post", channel, msg,
            "--agent", "transcripts-gateway",
            "--metadata", json.dumps({"trigger": "manual", "source": "transcripts", "file": filename}),
        )
    except subprocess.CalledProcessError as exc:
        print(f"[transcripts] failed to post {filename}: {exc.stderr}", file=sys.stderr)


def scan_directory(directory: Path, posted: set[str]) -> list[tuple[Path, str]]:
    """Find .md files not yet posted. Returns list of (path, hash)."""
    results = []
    if not directory.exists():
        print(f"[transcripts] directory {directory} does not exist", file=sys.stderr)
        return results

    for md_file in sorted(directory.glob("*.md")):
        if md_file.name.startswith("."):
            continue
        fh = file_hash(md_file)
        if fh not in posted:
            results.append((md_file, fh))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Transcripts gateway — posts .md transcript files to the bus"
    )
    parser.add_argument(
        "--directory",
        default=None,
        help="Directory to watch for .md transcript files",
    )
    parser.add_argument("--channel", default="meetings", help="Channel to post to (default: meetings)")
    parser.add_argument("--dry-run", action="store_true", help="Scan but don't post")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent

    transcript_dir = Path(args.directory) if args.directory else project_root / "transcripts"
    state_path = project_root / ".drifter" / "transcripts-gateway.state"

    posted = load_state(state_path)
    new_files = scan_directory(transcript_dir, posted)

    if not new_files:
        print("[transcripts] no new transcript files")
        return 0

    print(f"[transcripts] found {len(new_files)} new file(s)")

    if args.dry_run:
        for path, _ in new_files:
            print(f"  - {path.name}")
        return 0

    for path, fh in new_files:
        content = path.read_text()
        post_transcript(project_root, path.name, content, args.channel)
        posted.add(fh)
        print(f"[transcripts] posted {path.name} to #{args.channel}")

    save_state(state_path, posted)
    print(f"[transcripts] done — {len(new_files)} file(s) posted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
