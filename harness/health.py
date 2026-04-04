from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from harness.common import agent_paths, ensure_agent_files, load_state, run_drifter


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def inspect(agent: str) -> dict:
    paths = agent_paths(agent)
    ensure_agent_files(paths)
    state = load_state(paths)
    heartbeat = paths.heartbeat_path.read_text(encoding="utf-8").strip() if paths.heartbeat_path.exists() else ""
    tensions = paths.tensions_path.read_text(encoding="utf-8").strip() if paths.tensions_path.exists() else ""
    now = datetime.now(timezone.utc)
    last_cycle_at = parse_iso(state.get("last_cycle_at"))
    stalled = bool(last_cycle_at and now - last_cycle_at > timedelta(minutes=30))
    no_post_cycles = int(state.get("consecutive_cycles_without_post", 0))
    status = "healthy"
    if heartbeat == "die":
        status = "dead"
    elif heartbeat == "sleep":
        status = "paused"
    elif stalled or no_post_cycles >= 3:
        status = "blocked"
    return {
        "agent": agent,
        "status": status,
        "heartbeat": heartbeat or "running",
        "has_tensions": bool(tensions),
        "last_cycle_at": state.get("last_cycle_at"),
        "last_dream_at": state.get("last_dream_at"),
        "consecutive_cycles_without_post": no_post_cycles,
        "last_error": state.get("last_error"),
        "state_path": str(paths.state_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarise worker health.")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = inspect(args.agent)
    if args.json:
        print(json.dumps(report, indent=2))
        return
    print(f"{report['agent']}: {report['status']}")
    print(f"heartbeat={report['heartbeat']}")
    print(f"last_cycle_at={report['last_cycle_at']}")
    print(f"last_dream_at={report['last_dream_at']}")
    print(f"has_tensions={report['has_tensions']}")
    print(f"consecutive_cycles_without_post={report['consecutive_cycles_without_post']}")
    if report["last_error"]:
        print(f"last_error={report['last_error']}")


class CycleMetrics:
    """Tracks cycle health and writes metrics to SQLite."""

    def __init__(self, agent_name: str, db_path: Path):
        self.agent_name = agent_name
        self.db_path = db_path
        self.consecutive_silent = 0
        self.total_cycles = 0
        self.total_posts = 0
        self._cycle_start: float = 0.0
        self.last_cycle_duration: float = 0.0

    def cycle_start(self) -> None:
        self._cycle_start = time.monotonic()

    def cycle_end(self, posts_this_cycle: int) -> None:
        self.total_cycles += 1
        self.total_posts += posts_this_cycle
        self.last_cycle_duration = time.monotonic() - self._cycle_start
        if posts_this_cycle > 0:
            self.consecutive_silent = 0
        else:
            self.consecutive_silent += 1

    def is_stuck(self, threshold: int = 5) -> bool:
        return self.consecutive_silent >= threshold

    def record_metrics(self, cycle_id: str) -> None:
        rows = [
            ("cycle_duration_s", self.last_cycle_duration),
            ("consecutive_silent", float(self.consecutive_silent)),
            ("total_cycles", float(self.total_cycles)),
            ("total_posts", float(self.total_posts)),
        ]
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany(
                    "INSERT INTO metrics (agent_name, cycle_id, metric, value, context) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(self.agent_name, cycle_id, name, value,
                      json.dumps({"source": "harness.health"}))
                     for name, value in rows],
                )
        except sqlite3.Error:
            pass

    def notify_stuck(self, project_root: Path) -> None:
        try:
            run_drifter(
                project_root, "notify",
                f"Stuck: {self.agent_name}",
                f"{self.agent_name} has had {self.consecutive_silent} consecutive silent cycles",
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass


if __name__ == "__main__":
    main()
