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


def _get_inbox(paths, agent: str) -> list[dict]:
    """Fetch unacked inbox items. Returns [] on error."""
    try:
        items = run_drifter(paths.project_root, "inbox", agent, "--json", json_output=True)
        return items if isinstance(items, list) else []
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return []


def _ack_inbox(paths, items: list[dict]) -> None:
    """Ack inbox items directly without spawning a worker."""
    ids = [str(item["id"]) for item in items]
    if not ids:
        return
    try:
        run_drifter(paths.project_root, "ack", *ids)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def _has_rejected_branches(paths, agent: str) -> bool:
    """Check if this agent has branches in the rejected-branches file."""
    rejected_path = paths.project_root / ".drifter" / "rejected-branches"
    if not rejected_path.exists():
        return False
    prefix = f"agent/{agent}/"
    return any(line.startswith(prefix) for line in rejected_path.read_text().splitlines() if line.strip())


def _cooldown_elapsed(state: dict, minutes: int = 10) -> bool:
    """True if enough time has passed since the last regular cycle."""
    last = state.get("last_cycle_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - last_dt).total_seconds() >= minutes * 60
    except (ValueError, TypeError):
        return True


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


def _run_worker(agent: str, dream: bool = False, trigger: str = "regular") -> int:
    cmd = [sys.executable, "-m", "harness.worker", "--agent", agent]
    if dream:
        cmd.append("--dream")
    cmd.extend(["--trigger", trigger])
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
        # Priority 1: inbox
        inbox = _get_inbox(paths, args.agent)
        if inbox:
            has_actionable = any(
                item.get("from_agent") != "system" or item.get("msg_type", "system") != "system"
                for item in inbox
            )
            if has_actionable:
                _log("inbox has items")
                _run_worker(args.agent, trigger="inbox")
                return
            else:
                _log(f"acking {len(inbox)} system-only inbox items")
                _ack_inbox(paths, inbox)
                # fall through to dream check — don't return

        # Priority 2: rejected branches need attention (with cooldown)
        state = load_state(paths)
        if _has_rejected_branches(paths, args.agent) and _cooldown_elapsed(state):
            _log("rejected branches need attention")
            _run_worker(args.agent, trigger="rejected")
            return

        # Priority 3: dream deadline passed → dream cycle
        if _dream_due(state):
            _log("dream due")
            _run_worker(args.agent, dream=True, trigger="dream")
            return

        _log("nothing to do")
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == "__main__":
    main()
