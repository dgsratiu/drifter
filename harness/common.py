from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentPaths:
    project_root: Path
    agent_dir: Path
    memory_dir: Path
    dreams_dir: Path
    state_path: Path
    session_path: Path
    heartbeat_path: Path
    tensions_path: Path
    soul_path: Path
    config_path: Path
    memory_path: Path
    db_path: Path
    working_dir: Path | None = None


@dataclass(frozen=True)
class AgentConfig:
    name: str
    hypothesis: str
    model: str
    fallback_model: str | None
    dream_model: str
    immortal: bool
    watch_channels: list[str]
    post_channels: list[str]
    posts_per_minute: int
    sleep_idle: int
    sleep_active: int
    sleep_error: int
    dream_interval_hours: int


def resolve_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "rust" / "Cargo.toml").is_file() and (candidate / "docs" / "prd.md").is_file():
            return candidate
    raise FileNotFoundError(f"could not locate project root from {current}")


def agent_paths(agent: str, project_root: Path | None = None) -> AgentPaths:
    root = project_root or resolve_project_root()
    agent_dir = root / "agents" / agent
    memory_dir = agent_dir / "memory"
    return AgentPaths(
        project_root=root,
        agent_dir=agent_dir,
        memory_dir=memory_dir,
        dreams_dir=memory_dir / "dreams",
        state_path=agent_dir / "state.json",
        session_path=agent_dir / "session.md",
        heartbeat_path=agent_dir / "heartbeat.md",
        tensions_path=agent_dir / "tensions.md",
        soul_path=agent_dir / "AGENTS.md",
        config_path=agent_dir / "agent.toml",
        memory_path=memory_dir / "memory.md",
        db_path=root / "drifter.db",
    )


def ensure_agent_files(paths: AgentPaths) -> None:
    paths.memory_dir.mkdir(parents=True, exist_ok=True)
    paths.dreams_dir.mkdir(parents=True, exist_ok=True)
    for path in (paths.state_path, paths.session_path, paths.heartbeat_path, paths.tensions_path, paths.memory_path):
        if not path.exists():
            default = "{}" if path == paths.state_path else ""
            path.write_text(default, encoding="utf-8")


def _nested(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        if key not in current:
            return default
        current = current[key]
    return current


def load_agent_config(paths: AgentPaths) -> AgentConfig:
    raw = tomllib.loads(paths.config_path.read_text(encoding="utf-8"))
    name = _nested(raw, "agent", "name", default=paths.agent_dir.name)
    model = _nested(raw, "agent", "model", default="openrouter/auto")
    dream_model = _nested(raw, "agent", "dream_model", default=model)
    posts_per_minute = _nested(raw, "limits", "posts_per_minute", default=_nested(raw, "agent", "posts_per_minute", default=2))
    dream_interval_hours = _nested(raw, "worker", "dream_interval_hours", default=_nested(raw, "agent", "dream_interval_hours", default=4))
    return AgentConfig(
        name=name,
        hypothesis=_nested(raw, "agent", "hypothesis", default=""),
        model=model,
        fallback_model=_nested(raw, "agent", "fallback_model"),
        dream_model=dream_model,
        immortal=bool(_nested(raw, "agent", "immortal", default=False)),
        watch_channels=list(_nested(raw, "channels", "watch", default=["internal"])),
        post_channels=list(_nested(raw, "channels", "post", default=["internal"])),
        posts_per_minute=int(posts_per_minute),
        sleep_idle=int(_nested(raw, "worker", "sleep_idle", default=30)),
        sleep_active=int(_nested(raw, "worker", "sleep_active", default=5)),
        sleep_error=int(_nested(raw, "worker", "sleep_error", default=60)),
        dream_interval_hours=int(dream_interval_hours),
    )


def load_drifter_config(project_root: Path) -> dict[str, Any]:
    path = project_root / "drifter.toml"
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_state(paths: AgentPaths) -> dict[str, Any]:
    try:
        return json.loads(paths.state_path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


def resolve_working_dir(paths: AgentPaths) -> Path:
    """Return the agent's working directory from DB, falling back to project_root.

    If the agent has a working_dir in the DB, return that path relative to
    project_root. Otherwise return project_root (legacy agents without worktrees).
    """
    try:
        agents = run_drifter(paths.project_root, "agents", "--json", json_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return paths.project_root
    for agent in agents:
        if isinstance(agent, dict) and agent.get("name") == paths.agent_dir.name:
            wd = agent.get("working_dir")
            if wd:
                return paths.project_root / wd
    return paths.project_root


def save_state(paths: AgentPaths, state: dict[str, Any]) -> None:
    paths.state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iso_now() -> str:
    import datetime as _dt

    return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def drifter_bin(project_root: Path) -> str:
    candidates = [
        os.environ.get("DRIFTER_BIN"),
        shutil.which("drifter"),
        str(project_root / "rust" / "target" / "release" / "drifter"),
        str(project_root / "rust" / "target" / "debug" / "drifter"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    if shutil.which("drifter"):
        return "drifter"
    raise FileNotFoundError("could not locate drifter binary; set DRIFTER_BIN or build rust/target/{debug,release}/drifter")


def run_drifter(project_root: Path, *args: str, json_output: bool = False) -> Any:
    command = [drifter_bin(project_root), "--db", str(project_root / "drifter.db"), *args]
    result = subprocess.run(command, cwd=project_root, capture_output=True, text=True, check=True)
    output = result.stdout.strip()
    if json_output:
        return json.loads(output) if output else []
    return output


def opencode_bin() -> str:
    candidates = [os.environ.get("OPENCODE_BIN"), shutil.which("opencode")]
    for candidate in candidates:
        if candidate:
            return candidate
    raise FileNotFoundError("could not locate opencode binary; set OPENCODE_BIN or install it in PATH")

