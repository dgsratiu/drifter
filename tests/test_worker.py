import json
from pathlib import Path

import pytest

from harness.common import AgentConfig, AgentPaths
from harness.worker import (
    DreamCycleError,
    _extract_dream_bus_summary,
    run_dream_cycle,
)


@pytest.fixture
def worker_paths(tmp_path):
    agent_dir = tmp_path / "agents" / "engineer"
    memory_dir = agent_dir / "memory"
    dreams_dir = memory_dir / "dreams"
    dreams_dir.mkdir(parents=True)

    session_path = agent_dir / "session.md"
    session_path.write_text("old session\n", encoding="utf-8")
    tensions_path = agent_dir / "tensions.md"
    tensions_path.write_text("old tensions\n", encoding="utf-8")
    memory_path = memory_dir / "memory.md"
    memory_path.write_text("", encoding="utf-8")
    state_path = agent_dir / "state.json"
    state_path.write_text("{}", encoding="utf-8")
    heartbeat_path = agent_dir / "heartbeat.md"
    heartbeat_path.write_text("", encoding="utf-8")
    soul_path = agent_dir / "AGENTS.md"
    soul_path.write_text("# engineer\n", encoding="utf-8")
    config_path = agent_dir / "agent.toml"
    config_path.write_text("", encoding="utf-8")

    return AgentPaths(
        project_root=tmp_path,
        agent_dir=agent_dir,
        memory_dir=memory_dir,
        dreams_dir=dreams_dir,
        state_path=state_path,
        session_path=session_path,
        heartbeat_path=heartbeat_path,
        tensions_path=tensions_path,
        soul_path=soul_path,
        config_path=config_path,
        memory_path=memory_path,
        db_path=tmp_path / "drifter.db",
    )


@pytest.fixture
def worker_config():
    return AgentConfig(
        name="engineer",
        hypothesis="",
        model="model",
        fallback_model=None,
        dream_model="dream-model",
        immortal=True,
        watch_channels=["internal"],
        post_channels=["internal"],
        posts_per_minute=2,
        sleep_idle=30,
        sleep_active=5,
        sleep_error=60,
        dream_interval_hours=4,
    )


def test_extract_dream_bus_summary_prefers_explicit_section(tmp_path):
    dream_path = tmp_path / "dream.md"
    dream_path.write_text(
        "# Dream\n\n## Summary\nLonger summary.\n\n## Bus Summary\nShort bus summary.\n",
        encoding="utf-8",
    )

    assert _extract_dream_bus_summary(dream_path) == "Short bus summary."


def test_run_dream_cycle_posts_summary_from_harness(monkeypatch, worker_paths, worker_config):
    state = {}
    dream_path = worker_paths.dreams_dir / "2026-04-08-12.md"
    posted = {}

    monkeypatch.setattr("harness.worker.compile_dream_prompt", lambda paths, config: "prompt")
    monkeypatch.setattr("harness.worker.resolve_working_dir", lambda paths: paths.project_root)
    monkeypatch.setattr("harness.worker.iso_now", lambda: "2026-04-08T12:34:56Z")
    monkeypatch.setattr(
        "harness.worker.recent_self_posts",
        lambda paths, config: ([{"seq": 41, "channel": "dreams", "content": "summary"}], 41),
    )

    def fake_run_opencode_cycle(project_root, agent, model, prompt, working_dir):
        dream_path.write_text(
            "# Dream Cycle 2026-04-08-12\n\n"
            "## Bus Summary\nDeterministic dream summary.\n\n"
            "## Summary\nLonger detail.\n",
            encoding="utf-8",
        )
        worker_paths.tensions_path.write_text("new tensions\n", encoding="utf-8")
        worker_paths.session_path.write_text("new session\n", encoding="utf-8")

    def fake_run_drifter(project_root, *args, json_output=False):
        posted["args"] = args
        if json_output:
            return []
        return "[41] posted to #dreams"

    monkeypatch.setattr("harness.worker.run_opencode_cycle", fake_run_opencode_cycle)
    monkeypatch.setattr("harness.worker.run_drifter", fake_run_drifter)

    posts = run_dream_cycle(worker_paths, worker_config, state)

    assert posts == 1
    assert state["last_dream_at"] == "2026-04-08T12:34:56Z"
    assert state["last_trigger"] == "dream"
    assert state["last_post_seq"] == 41
    assert posted["args"] == (
        "post",
        "dreams",
        "Deterministic dream summary.",
        "--agent",
        "engineer",
        "--metadata",
        json.dumps({"trigger": "dream"}),
    )


def test_run_dream_cycle_rejects_partial_outputs(monkeypatch, worker_paths, worker_config):
    state = {}
    dream_path = worker_paths.dreams_dir / "2026-04-08-12.md"

    monkeypatch.setattr("harness.worker.compile_dream_prompt", lambda paths, config: "prompt")
    monkeypatch.setattr("harness.worker.resolve_working_dir", lambda paths: paths.project_root)
    monkeypatch.setattr("harness.worker.iso_now", lambda: "2026-04-08T12:34:56Z")
    monkeypatch.setattr("harness.worker.recent_self_posts", lambda paths, config: ([], 0))

    def fake_run_opencode_cycle(project_root, agent, model, prompt, working_dir):
        dream_path.write_text(
            "# Dream Cycle 2026-04-08-12\n\n## Bus Summary\nPartial dream.\n",
            encoding="utf-8",
        )

    monkeypatch.setattr("harness.worker.run_opencode_cycle", fake_run_opencode_cycle)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("run_drifter should not be called for invalid dream outputs")

    monkeypatch.setattr("harness.worker.run_drifter", fail_if_called)

    with pytest.raises(DreamCycleError):
        run_dream_cycle(worker_paths, worker_config, state)

    assert "last_dream_at" not in state
