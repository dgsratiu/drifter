from __future__ import annotations

import argparse
import fcntl
import os
import signal
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# inotify for sub-second wake; falls back to polling
try:
    import inotify_simple
    _HAS_INOTIFY = True
except ImportError:
    _HAS_INOTIFY = False

_shutdown_requested = False


def _handle_sigterm(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True


from harness.common import (
    agent_paths, ensure_agent_files, iso_now, load_agent_config,
    load_drifter_config, load_state, opencode_bin, run_drifter, save_state,
)
from harness.health import CycleMetrics
from harness.memory import compile_dream_prompt, compile_regular_prompt, recent_self_posts


def heartbeat_mode(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip().lower()


def nonempty_tensions(path: Path) -> bool:
    return path.exists() and bool(path.read_text(encoding="utf-8").strip())


@contextmanager
def opencode_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)



def opencode_env(project_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    # Put the drifter binary on PATH so OpenCode can run drifter commands
    release_dir = str(project_root / "rust" / "target" / "release")
    env["PATH"] = release_dir + os.pathsep + env.get("PATH", "")
    llm = load_drifter_config(project_root).get("llm", {})
    provider = llm.get("provider")
    api_key = llm.get("api_key")
    if provider == "openrouter" and api_key:
        env.setdefault("OPENROUTER_API_KEY", api_key)
    return env


def _rotate_logs(log_dir: Path, keep: int = 200) -> None:
    """Keep the most recent `keep` log files, delete the rest."""
    logs = sorted(log_dir.glob("*.log"))
    for stale in logs[:-keep]:
        stale.unlink(missing_ok=True)


def run_opencode_cycle(project_root: Path, agent: str, model: str, prompt: str) -> None:
    log_dir = project_root / ".drifter" / "logs" / agent
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"{timestamp}.log"

    with opencode_lock(project_root / ".drifter-opencode.lock"):
        with tempfile.NamedTemporaryFile("w", suffix=".md", prefix="drifter-prompt-", dir=project_root, delete=False, encoding="utf-8") as handle:
            handle.write(prompt)
            prompt_path = Path(handle.name)
        try:
            command = [opencode_bin(), "run", "--model", model, f"Read {prompt_path} and follow instructions"]
            with log_path.open("w", encoding="utf-8") as log_file:
                subprocess.run(command, cwd=project_root, env=opencode_env(project_root),
                               stdout=log_file, stderr=subprocess.STDOUT, check=True)
        finally:
            prompt_path.unlink(missing_ok=True)
            _rotate_logs(log_dir)


def delete_wake_file(agent_dir: Path) -> None:
    (agent_dir / ".wake").unlink(missing_ok=True)


# ── Wake file watching ────────────────────────────────────────────────────


def _wait_for_event(wake_path: Path, poll_interval: float, dream_deadline: float) -> str:
    """Block until a wake event, poll timeout, or dream timer fires.

    Returns: 'wake', 'poll', or 'dream'.
    """
    if wake_path.exists():
        return "wake"
    if _HAS_INOTIFY:
        return _wait_inotify(wake_path, poll_interval, dream_deadline)
    return _wait_poll(wake_path, poll_interval, dream_deadline)


def _wait_inotify(wake_path: Path, poll_interval: float, dream_deadline: float) -> str:
    watch_dir = wake_path.parent
    watch_dir.mkdir(parents=True, exist_ok=True)
    inotify = inotify_simple.INotify()
    inotify.add_watch(str(watch_dir), inotify_simple.flags.CREATE | inotify_simple.flags.MODIFY)
    try:
        while True:
            now = time.monotonic()
            if now >= dream_deadline:
                return "dream"
            if wake_path.exists():
                return "wake"
            timeout_ms = int(min(poll_interval, dream_deadline - now) * 1000)
            events = inotify.read(timeout=timeout_ms)
            if wake_path.exists():
                return "wake"
            if not events:
                return "poll"
    finally:
        inotify.close()


def _wait_poll(wake_path: Path, poll_interval: float, dream_deadline: float) -> str:
    deadline = time.monotonic() + poll_interval
    while True:
        now = time.monotonic()
        if now >= dream_deadline:
            return "dream"
        if wake_path.exists():
            return "wake"
        if now >= deadline:
            return "poll"
        time.sleep(min(2.0, deadline - now, dream_deadline - now))


# ── Dream deadline ────────────────────────────────────────────────────────


def _dream_deadline(state: dict, interval_hours: int) -> float:
    """Return monotonic time when next dream is due.  inf if disabled."""
    if interval_hours <= 0:
        return float("inf")
    interval_s = interval_hours * 3600
    last_dream = state.get("last_dream_at")
    if not last_dream:
        return time.monotonic()
    try:
        last_dt = datetime.fromisoformat(str(last_dream).replace("Z", "+00:00"))
        elapsed = max(0.0, (datetime.now(timezone.utc) - last_dt).total_seconds())
        return time.monotonic() + max(0.0, interval_s - elapsed)
    except (ValueError, TypeError):
        return time.monotonic()


# ── Cycle helpers ─────────────────────────────────────────────────────────


def update_post_metrics(paths, config, state, before_max_seq: int) -> int:
    """Update state post counters.  Returns posts this cycle (0 or 1+)."""
    _, after_max_seq = recent_self_posts(paths, config)
    if after_max_seq > before_max_seq:
        state["consecutive_cycles_without_post"] = 0
        state["last_post_seq"] = after_max_seq
        return 1
    state["consecutive_cycles_without_post"] = int(state.get("consecutive_cycles_without_post", 0)) + 1
    return 0


def run_regular_cycle(paths, config, state, force: bool = False) -> tuple[bool, int]:
    """Returns (worked, posts_this_cycle).  force=True runs even with no inbox/deltas."""
    bundle = compile_regular_prompt(paths, config, state)
    if not bundle.has_work and not force:
        return False, 0
    run_opencode_cycle(paths.project_root, config.name, config.model, bundle.text)
    state["channel_cursors"] = bundle.channel_cursors
    state["last_cycle_at"] = iso_now()
    state["last_trigger"] = "browse" if force and not bundle.has_work else "regular"
    posts = update_post_metrics(paths, config, state, bundle.self_post_max_seq)
    return True, posts


def run_dream_cycle(paths, config, state) -> int:
    """Returns posts this cycle."""
    prompt = compile_dream_prompt(paths, config)
    _, before_max_seq = recent_self_posts(paths, config)
    run_opencode_cycle(paths.project_root, config.name, config.dream_model, prompt)
    state["last_dream_at"] = iso_now()
    state["last_cycle_at"] = state["last_dream_at"]
    state["last_trigger"] = "dream"
    return update_post_metrics(paths, config, state, before_max_seq)


# ── Main loop ─────────────────────────────────────────────────────────────


def ensure_agent_registered(paths, config) -> None:
    """Register agent in drifter DB if not already present."""
    try:
        agents = run_drifter(paths.project_root, "agents", "--json", json_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return  # drifter binary not available or DB not initialized
    registered = {a["name"] for a in agents if isinstance(a, dict)}
    if config.name in registered:
        return

    birth_cmd = ["birth", config.name, "--soul", str(paths.soul_path), "--model", config.model]
    if config.immortal:
        birth_cmd.append("--immortal")
    try:
        run_drifter(paths.project_root, *birth_cmd)
    except subprocess.CalledProcessError:
        pass  # may fail if DB not initialized; worker will retry next startup

    for channel in config.watch_channels:
        try:
            run_drifter(paths.project_root, "watch", config.name, channel)
        except subprocess.CalledProcessError:
            pass


def loop(agent: str, once: bool = False, force_dream: bool = False) -> int:
    signal.signal(signal.SIGTERM, _handle_sigterm)
    paths = agent_paths(agent)
    ensure_agent_files(paths)
    config = load_agent_config(paths)
    ensure_agent_registered(paths, config)
    state = load_state(paths)
    metrics = CycleMetrics(config.name, paths.db_path)

    wake_path = paths.agent_dir / ".wake"
    dream_dl = _dream_deadline(state, config.dream_interval_hours)
    last_cycle_at = 0.0  # monotonic; 0 ensures first cycle fires immediately

    while True:
        if _shutdown_requested:
            state["worker_status"] = "terminated"
            save_state(paths, state)
            return 0

        mode = heartbeat_mode(paths.heartbeat_path)
        if mode == "die":
            state["worker_status"] = "dead"
            save_state(paths, state)
            delete_wake_file(paths.agent_dir)
            return 0
        if mode == "sleep":
            state["worker_status"] = "sleep"
            state["last_polled_at"] = iso_now()
            save_state(paths, state)
            delete_wake_file(paths.agent_dir)
            if once:
                return 0
            time.sleep(config.sleep_idle)
            continue
        if mode == "blocked":
            state["worker_status"] = "blocked"
            state["last_polled_at"] = iso_now()
            save_state(paths, state)
            delete_wake_file(paths.agent_dir)
            if once:
                return 0
            time.sleep(config.sleep_error)
            continue

        # Determine event
        if force_dream:
            event = "dream"
            force_dream = False
        elif once:
            event = "dream" if time.monotonic() >= dream_dl else "poll"
        else:
            event = _wait_for_event(wake_path, config.sleep_idle, dream_dl)

        cycle_id = uuid.uuid4().hex[:8]

        try:
            if event == "dream":
                metrics.cycle_start()
                posts = run_dream_cycle(paths, config, state)
                metrics.cycle_end(posts)
                metrics.record_metrics(cycle_id)
                last_cycle_at = time.monotonic()
                dream_dl = last_cycle_at + config.dream_interval_hours * 3600
            else:
                delete_wake_file(paths.agent_dir)
                idle_elapsed = time.monotonic() - last_cycle_at
                browse = idle_elapsed >= config.sleep_idle
                metrics.cycle_start()
                worked, posts = run_regular_cycle(paths, config, state, force=browse)
                if not worked:
                    state["worker_status"] = "idle"
                    state["last_polled_at"] = iso_now()
                    save_state(paths, state)
                    if once:
                        return 0
                    continue
                last_cycle_at = time.monotonic()
                metrics.cycle_end(posts)
                metrics.record_metrics(cycle_id)
                if metrics.is_stuck():
                    metrics.notify_stuck(paths.project_root)
        except subprocess.CalledProcessError as exc:
            state["worker_status"] = "error"
            state["last_error_at"] = iso_now()
            state["last_error"] = exc.stderr.strip() if exc.stderr else str(exc)
            save_state(paths, state)
            delete_wake_file(paths.agent_dir)
            if once:
                return 1
            time.sleep(config.sleep_error)
            continue
        except FileNotFoundError as exc:
            state["worker_status"] = "error"
            state["last_error_at"] = iso_now()
            state["last_error"] = str(exc)
            save_state(paths, state)
            return 1

        state["worker_status"] = "idle"
        state["last_polled_at"] = iso_now()
        save_state(paths, state)
        delete_wake_file(paths.agent_dir)
        if once:
            return 0
        force_dream = False
        time.sleep(config.sleep_active)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Drifter worker loop.")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dream", action="store_true")
    args = parser.parse_args()
    raise SystemExit(loop(args.agent, once=args.once, force_dream=args.dream))


if __name__ == "__main__":
    main()
