from __future__ import annotations

import argparse
import fcntl
import os
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from harness.common import (
    agent_paths, ensure_agent_files, iso_now, load_agent_config,
    load_drifter_config, load_state, opencode_bin, resolve_working_dir,
    run_drifter, save_state,
)
from harness.health import CycleMetrics
from harness.memory import compile_dream_prompt, compile_regular_prompt, recent_self_posts


SESSION_TIMEOUT = 1800  # 30 minutes
MAX_CONSECUTIVE_FAILURES = 3


def _log(agent: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{agent}] {msg}", flush=True)


def _post_failure_alert(paths, agent: str, failures: int, err_msg: str) -> None:
    try:
        run_drifter(
            paths.project_root, "post", "engineering",
            f"{agent}: {failures} consecutive failures, backing off — last error: {err_msg}",
            "--agent", agent, "--type", "error",
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


@contextmanager
def opencode_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def opencode_env(project_root: Path, agent: str) -> dict[str, str]:
    release_dir = str(project_root / "rust" / "target" / "release")
    agent_email = f"{agent}@drifter.local"
    env = {
        "PATH": release_dir + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(project_root)),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TERM": "dumb",
        "DRIFTER_AGENT": agent,
        "GIT_AUTHOR_NAME": agent,
        "GIT_AUTHOR_EMAIL": agent_email,
        "GIT_COMMITTER_NAME": agent,
        "GIT_COMMITTER_EMAIL": agent_email,
    }
    llm = load_drifter_config(project_root).get("llm", {})
    provider = llm.get("provider")
    api_key = llm.get("api_key")
    if provider == "openrouter" and api_key:
        env["OPENROUTER_API_KEY"] = api_key
    return env


def _write_timeout_handoff(paths, agent: str) -> None:
    """Capture log tail as session handoff after a timeout."""
    log_dir = paths.project_root / ".drifter" / "logs" / agent
    logs = sorted(log_dir.glob("*.log"))
    if not logs:
        return
    try:
        lines = logs[-1].read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-40:])
    except OSError:
        return
    handoff = (
        "# Session Handoff (auto-generated — previous cycle timed out)\n\n"
        "## Last activity before timeout\n"
        f"```\n{tail}\n```\n\n"
        "## Status\n"
        "Previous cycle timed out. Continue from where it left off. "
        "Do NOT repeat completed steps.\n"
    )
    paths.session_path.write_text(handoff, encoding="utf-8")


def _rotate_logs(log_dir: Path, keep: int = 200) -> None:
    logs = sorted(log_dir.glob("*.log"))
    for stale in logs[:-keep]:
        stale.unlink(missing_ok=True)


def _tee_output(proc, log_file):
    for chunk in iter(lambda: proc.stdout.read(4096), b""):
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        log_file.write(chunk)


def run_opencode_cycle(project_root: Path, agent: str, model: str, prompt: str, working_dir: Path | None = None) -> None:
    log_dir = project_root / ".drifter" / "logs" / agent
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"{timestamp}.log"

    cwd = working_dir if working_dir and working_dir.exists() else project_root
    lock_path = project_root / ".drifter" / "locks" / f"{agent}.lock"

    with opencode_lock(lock_path):
        with tempfile.NamedTemporaryFile("w", suffix=".md", prefix="drifter-prompt-", dir=cwd, delete=False, encoding="utf-8") as handle:
            handle.write(prompt)
            prompt_path = Path(handle.name)
        try:
            command = [opencode_bin(), "run", "--model", model, f"Read {prompt_path} and follow instructions"]
            with log_path.open("wb") as log_file:
                proc = subprocess.Popen(command, cwd=cwd, env=opencode_env(project_root, agent),
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                tee = threading.Thread(target=_tee_output, args=(proc, log_file), daemon=True)
                tee.start()
                try:
                    proc.wait(timeout=SESSION_TIMEOUT)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    log_file.write(f"\n[worker] OpenCode timed out after {SESSION_TIMEOUT}s\n".encode())
                    raise
                tee.join(timeout=5)
                if proc.returncode:
                    raise subprocess.CalledProcessError(proc.returncode, command)
        finally:
            prompt_path.unlink(missing_ok=True)
            _rotate_logs(log_dir)


# ── Cycle helpers ─────────────────────────────────────────────────────────


def _ack_inbox(paths, inbox_ids: list[int]) -> None:
    for item_id in inbox_ids:
        try:
            run_drifter(paths.project_root, "ack", str(item_id))
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass


def run_regular_cycle(paths, config, state) -> tuple[bool, int]:
    """Returns (worked, posts_this_cycle)."""
    bundle = compile_regular_prompt(paths, config, state)
    if not bundle.has_work:
        return False, 0
    working_dir = resolve_working_dir(paths)
    try:
        run_opencode_cycle(paths.project_root, config.name, config.model, bundle.text, working_dir)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        err_msg = (getattr(exc, "stderr", None) or "").strip() or str(exc)
        consecutive = int(state.get("consecutive_failures", 0)) + 1
        state["consecutive_failures"] = consecutive
        if consecutive >= MAX_CONSECUTIVE_FAILURES:
            _log(config.name, f"cycle failed {consecutive} times, acking and backing off: {err_msg}")
            _ack_inbox(paths, bundle.inbox_ids)
            _post_failure_alert(paths, config.name, consecutive, err_msg)
        else:
            _log(config.name, f"cycle failed ({consecutive}/{MAX_CONSECUTIVE_FAILURES}), NOT acking — will retry: {err_msg}")
        raise
    _ack_inbox(paths, bundle.inbox_ids)
    _log(config.name, f"cycle succeeded, acking {len(bundle.inbox_ids)} inbox items")
    state["consecutive_failures"] = 0
    state["channel_cursors"] = bundle.channel_cursors
    state["last_cycle_at"] = iso_now()
    state["last_trigger"] = "regular"
    _, after_max_seq = recent_self_posts(paths, config)
    if after_max_seq > bundle.self_post_max_seq:
        state["consecutive_cycles_without_post"] = 0
        state["last_post_seq"] = after_max_seq
        return True, 1
    state["consecutive_cycles_without_post"] = int(state.get("consecutive_cycles_without_post", 0)) + 1
    return True, 0


def run_dream_cycle(paths, config, state) -> int:
    """Returns posts this cycle."""
    prompt = compile_dream_prompt(paths, config)
    _, before_max_seq = recent_self_posts(paths, config)
    working_dir = resolve_working_dir(paths)
    run_opencode_cycle(paths.project_root, config.name, config.dream_model, prompt, working_dir)
    state["last_dream_at"] = iso_now()
    state["last_cycle_at"] = state["last_dream_at"]
    state["last_trigger"] = "dream"
    _, after_max_seq = recent_self_posts(paths, config)
    if after_max_seq > before_max_seq:
        state["consecutive_cycles_without_post"] = 0
        state["last_post_seq"] = after_max_seq
        return 1
    state["consecutive_cycles_without_post"] = int(state.get("consecutive_cycles_without_post", 0)) + 1
    return 0


# ── Entry point ──────────────────────────────────────────────────────────


def ensure_agent_registered(paths, config) -> None:
    try:
        agents = run_drifter(paths.project_root, "agents", "--json", json_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return
    registered = {a["name"] for a in agents if isinstance(a, dict)}
    if config.name in registered:
        return

    birth_cmd = ["birth", config.name, "--soul", str(paths.soul_path), "--model", config.model]
    if config.immortal:
        birth_cmd.append("--immortal")
    try:
        run_drifter(paths.project_root, *birth_cmd)
    except subprocess.CalledProcessError:
        pass

    for channel in config.watch_channels:
        try:
            run_drifter(paths.project_root, "watch", config.name, channel)
        except subprocess.CalledProcessError:
            pass


def run(agent: str, dream: bool = False) -> int:
    """Run one cycle for the agent, then exit."""
    paths = agent_paths(agent)
    ensure_agent_files(paths)
    config = load_agent_config(paths)
    ensure_agent_registered(paths, config)
    state = load_state(paths)
    metrics = CycleMetrics(config.name, paths.db_path)
    cycle_id = uuid.uuid4().hex[:8]

    try:
        if dream:
            metrics.cycle_start()
            posts = run_dream_cycle(paths, config, state)
            metrics.cycle_end(posts)
            metrics.record_metrics(cycle_id)
        else:
            metrics.cycle_start()
            worked, posts = run_regular_cycle(paths, config, state)
            if not worked:
                state["worker_status"] = "idle"
                state["last_polled_at"] = iso_now()
                save_state(paths, state)
                return 0
            metrics.cycle_end(posts)
            metrics.record_metrics(cycle_id)
            if metrics.is_stuck():
                metrics.notify_stuck(paths.project_root)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        state["worker_status"] = "error"
        state["last_error_at"] = iso_now()
        state["last_error"] = (getattr(exc, "stderr", None) or "").strip() or str(exc)
        if isinstance(exc, subprocess.TimeoutExpired):
            _write_timeout_handoff(paths, config.name)
        save_state(paths, state)
        return 1
    except FileNotFoundError as exc:
        state["worker_status"] = "error"
        state["last_error_at"] = iso_now()
        state["last_error"] = str(exc)
        save_state(paths, state)
        return 1

    state["worker_status"] = "idle"
    state["last_polled_at"] = iso_now()
    save_state(paths, state)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Drifter agent cycle.")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--dream", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(args.agent, dream=args.dream))


if __name__ == "__main__":
    main()
