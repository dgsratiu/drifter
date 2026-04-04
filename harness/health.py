from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from harness.common import agent_paths, ensure_agent_files, load_state


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


if __name__ == "__main__":
    main()
