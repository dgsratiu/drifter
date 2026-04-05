"""Cron scheduler for Drifter agents.

Runs every 2 minutes via cron. Checks inbox and dream deadline,
starts the worker for the appropriate cycle type. Uses flock to
prevent concurrent runs.

Usage: python3 -m harness.scheduler --agent engineer
"""
from __future__ import annotations

import argparse
import fcntl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from harness.common import agent_paths, ensure_agent_files, load_state, run_drifter


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def _has_inbox(paths, agent: str) -> bool:
    try:
        items = run_drifter(paths.project_root, "inbox", agent, "--json", json_output=True)
        return bool(items)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _dream_due(state: dict, interval_hours: int = 4) -> bool:
    last = state.get("last_dream_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
        return elapsed >= interval_hours * 3600
    except (ValueError, TypeError):
        return True


def _run_worker(agent: str, dream: bool = False) -> int:
    cmd = [sys.executable, "-m", "harness.worker", "--agent", agent]
    if dream:
        cmd.append("--dream")
    _log(f"starting: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    _log(f"worker exited with code {result.returncode}")
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="Drifter agent scheduler.")
    parser.add_argument("--agent", required=True)
    args = parser.parse_args()

    paths = agent_paths(args.agent)
    ensure_agent_files(paths)

    # Flock to prevent concurrent scheduler runs
    lock_path = paths.project_root / ".drifter" / "locks" / "scheduler.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = lock_path.open("w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _log("scheduler already running")
        return

    try:
        # Priority 1: inbox has items → work cycle
        if _has_inbox(paths, args.agent):
            _log("inbox has items")
            _run_worker(args.agent)
            return

        # Priority 2: dream deadline passed → dream cycle
        state = load_state(paths)
        if _dream_due(state):
            _log("dream due")
            _run_worker(args.agent, dream=True)
            return

        _log("nothing to do")
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == "__main__":
    main()
