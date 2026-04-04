from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from harness.common import agent_paths, ensure_agent_files, iso_now, load_agent_config, load_drifter_config, load_state, opencode_bin, save_state
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


def write_opencode_config(project_root: Path, model: str) -> Path:
    config = load_drifter_config(project_root)
    llm = config.get("llm", {})
    payload = {
        "provider": llm.get("provider", "openrouter"),
        "model": model,
    }
    path = project_root / "opencode.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def opencode_env(project_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    llm = load_drifter_config(project_root).get("llm", {})
    provider = llm.get("provider")
    api_key = llm.get("api_key")
    if provider == "openrouter" and api_key:
        env.setdefault("OPENROUTER_API_KEY", api_key)
    return env


def run_opencode_cycle(project_root: Path, model: str, prompt: str) -> None:
    backup_path = project_root / "opencode.json.bak"
    config_path = project_root / "opencode.json"
    had_existing = config_path.exists()
    if had_existing:
        backup_path.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")

    with opencode_lock(project_root / ".drifter-opencode.lock"):
        write_opencode_config(project_root, model)
        with tempfile.NamedTemporaryFile("w", suffix=".md", prefix="drifter-prompt-", dir=project_root, delete=False, encoding="utf-8") as handle:
            handle.write(prompt)
            prompt_path = Path(handle.name)
        try:
            command = [opencode_bin(), "run", "--auto", f"Read {prompt_path} and follow instructions"]
            subprocess.run(command, cwd=project_root, env=opencode_env(project_root), check=True)
        finally:
            prompt_path.unlink(missing_ok=True)
            if had_existing and backup_path.exists():
                config_path.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
                backup_path.unlink(missing_ok=True)
            elif not had_existing:
                config_path.unlink(missing_ok=True)


def delete_wake_file(path: Path) -> None:
    (path / ".wake").unlink(missing_ok=True)


def update_post_metrics(paths, config, state, before_max_seq: int) -> None:
    _, after_max_seq = recent_self_posts(paths, config)
    if after_max_seq > before_max_seq:
        state["consecutive_cycles_without_post"] = 0
        state["last_post_seq"] = after_max_seq
    else:
        state["consecutive_cycles_without_post"] = int(state.get("consecutive_cycles_without_post", 0)) + 1


def run_regular_cycle(paths, config, state) -> bool:
    bundle = compile_regular_prompt(paths, config, state)
    if not bundle.has_work:
        return False
    run_opencode_cycle(paths.project_root, config.model, bundle.text)
    state["channel_cursors"] = bundle.channel_cursors
    state["last_cycle_at"] = iso_now()
    state["last_trigger"] = "regular"
    update_post_metrics(paths, config, state, bundle.self_post_max_seq)
    if bundle.inbox_ids:
        from harness.common import run_drifter

        run_drifter(paths.project_root, "ack", *(str(item) for item in bundle.inbox_ids))
        state["last_acked_inbox_ids"] = bundle.inbox_ids
    return True


def run_dream_cycle(paths, config, state) -> bool:
    prompt = compile_dream_prompt(paths, config)
    _, before_max_seq = recent_self_posts(paths, config)
    run_opencode_cycle(paths.project_root, config.dream_model, prompt)
    state["last_dream_at"] = iso_now()
    state["last_cycle_at"] = state["last_dream_at"]
    state["last_trigger"] = "dream"
    update_post_metrics(paths, config, state, before_max_seq)
    return True


def loop(agent: str, once: bool = False, force_dream: bool = False) -> int:
    paths = agent_paths(agent)
    ensure_agent_files(paths)
    config = load_agent_config(paths)
    state = load_state(paths)

    while True:
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

        wake_exists = (paths.agent_dir / ".wake").exists()
        dream_due = force_dream
        if not dream_due and config.dream_interval_hours > 0:
            last_dream = state.get("last_dream_at")
            if not last_dream:
                dream_due = True
            else:
                from datetime import datetime, timedelta, timezone

                last_dt = datetime.fromisoformat(str(last_dream).replace("Z", "+00:00"))
                dream_due = datetime.now(timezone.utc) >= last_dt + timedelta(hours=config.dream_interval_hours)

        try:
            if dream_due:
                run_dream_cycle(paths, config, state)
                delay = config.sleep_active
            elif wake_exists or nonempty_tensions(paths.tensions_path) or once:
                worked = run_regular_cycle(paths, config, state)
                delay = config.sleep_active if worked else config.sleep_idle
            else:
                state["last_polled_at"] = iso_now()
                state["worker_status"] = "idle"
                save_state(paths, state)
                delete_wake_file(paths.agent_dir)
                if once:
                    return 0
                time.sleep(config.sleep_idle)
                continue
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
        time.sleep(delay)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Drifter worker loop.")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dream", action="store_true")
    args = parser.parse_args()
    raise SystemExit(loop(args.agent, once=args.once, force_dream=args.dream))


if __name__ == "__main__":
    main()
