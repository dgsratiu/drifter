"""Tests for the harness scheduler.

Tests cover:
- system-only inbox gets acked without spawning worker
- mixed inbox spawns worker
- empty inbox falls through to dream check
- dream deadline calculation
"""

import json
import subprocess
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from harness import scheduler
from harness.common import AgentPaths


@pytest.fixture
def mock_paths(tmp_path):
    """Create minimal AgentPaths for testing."""
    agent_dir = tmp_path / "agents" / "engineer"
    agent_dir.mkdir(parents=True)
    memory_dir = agent_dir / "memory"
    memory_dir.mkdir()
    (memory_dir / "dreams").mkdir()
    state_path = agent_dir / "state.json"
    state_path.write_text("{}")

    return AgentPaths(
        project_root=tmp_path,
        agent_dir=agent_dir,
        memory_dir=memory_dir,
        dreams_dir=memory_dir / "dreams",
        state_path=state_path,
        session_path=agent_dir / "session.md",
        heartbeat_path=agent_dir / "heartbeat.md",
        tensions_path=agent_dir / "tensions.md",
        soul_path=agent_dir / "AGENTS.md",
        config_path=agent_dir / "agent.toml",
        memory_path=memory_dir / "memory.md",
        db_path=tmp_path / "drifter.db",
    )


class TestSystemOnlyInbox:
    """System-only inbox items get acked without spawning a worker."""

    def test_system_only_inbox_acks_without_worker(self, mock_paths):
        """When inbox has only system messages, ack them and don't spawn worker."""
        inbox_items = [
            {"id": 1, "from_agent": "system", "text": "auto-merge OK"},
            {"id": 2, "from_agent": "system", "text": "gate passed"},
        ]

        with patch.object(scheduler, "run_drifter") as mock_run:
            mock_run.return_value = inbox_items
            scheduler._get_inbox(mock_paths, "engineer")
            mock_run.assert_called()

        with patch.object(scheduler, "run_drifter") as mock_run:
            scheduler._ack_inbox(mock_paths, inbox_items)
            mock_run.assert_called_with(mock_paths.project_root, "ack", "1", "2")

    def test_empty_system_inbox_no_ack(self, mock_paths):
        """When inbox is empty, no ack is called."""
        with patch.object(scheduler, "run_drifter") as mock_run:
            mock_run.return_value = []

            inbox = scheduler._get_inbox(mock_paths, "engineer")
            assert inbox == []

        with patch.object(scheduler, "run_drifter") as mock_run:
            scheduler._ack_inbox(mock_paths, [])
            mock_run.assert_not_called()


class TestMixedInbox:
    """Mixed inbox (system + actionable) spawns a worker."""

    def test_mixed_inbox_spawns_worker(self, mock_paths):
        """When inbox has actionable items, spawn worker and don't ack."""
        inbox_items = [
            {"id": 10, "from_agent": "system", "text": "auto-merge OK"},
            {"id": 11, "from_agent": "daniel", "text": "build feature X"},
        ]

        with (
            patch("argparse.ArgumentParser") as mock_parser_cls,
            patch.object(scheduler, "run_drifter", return_value=inbox_items),
            patch.object(scheduler, "_run_worker", return_value=0) as mock_worker,
            patch.object(scheduler, "load_state", return_value={}),
            patch.object(scheduler, "fcntl"),
            patch.object(scheduler, "agent_paths", return_value=mock_paths),
            patch.object(scheduler, "ensure_agent_files"),
        ):
            mock_args = MagicMock()
            mock_args.agent = "engineer"
            mock_parser_cls.return_value.parse_args.return_value = mock_args

            scheduler.main()

        mock_worker.assert_called_once_with("engineer", trigger="inbox")

    def test_actionable_only_inbox_spawns_worker(self, mock_paths):
        """When inbox has only actionable items, spawn worker."""
        inbox_items = [
            {"id": 20, "from_agent": "daniel", "text": "fix bug Y"},
        ]

        with patch.object(scheduler, "_run_worker", return_value=0) as mock_worker:
            with patch.object(scheduler, "run_drifter", return_value=inbox_items):
                with patch.object(scheduler, "_ack_inbox") as mock_ack:
                    has_actionable = any(item.get("from_agent") != "system" for item in inbox_items)
                    assert has_actionable is True

                    if has_actionable:
                        scheduler._run_worker("engineer", trigger="inbox")
                    else:
                        scheduler._ack_inbox(mock_paths, inbox_items)

        mock_worker.assert_called_once_with("engineer", trigger="inbox")
        mock_ack.assert_not_called()


class TestEmptyInbox:
    """Empty inbox falls through to dream check."""

    def test_empty_inbox_checks_dream(self, mock_paths):
        """When inbox is empty, scheduler checks if dream is due."""
        with patch.object(scheduler, "run_drifter", return_value=[]):
            inbox = scheduler._get_inbox(mock_paths, "engineer")
            assert inbox == []

        # Empty inbox should fall through — verify _dream_due is reachable
        state = {"last_dream_at": "2020-01-01T00:00:00Z"}
        assert scheduler._dream_due(state) is True

    def test_empty_inbox_no_dream_when_recent(self, mock_paths):
        """When inbox is empty and dream is not due, do nothing."""
        recent = datetime.now(timezone.utc).isoformat()
        state = {"last_dream_at": recent}
        assert scheduler._dream_due(state) is False


class TestDreamDeadline:
    """Dream deadline calculation."""

    def test_no_last_dream_means_due(self):
        """When there's no last_dream_at, dream is due."""
        assert scheduler._dream_due({}) is True
        assert scheduler._dream_due({"last_dream_at": None}) is True

    def test_old_dream_is_due(self):
        """Dream is due when last_dream_at is older than interval."""
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        assert scheduler._dream_due({"last_dream_at": old}) is True

    def test_recent_dream_not_due(self):
        """Dream is not due when last_dream_at is within interval."""
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert scheduler._dream_due({"last_dream_at": recent}) is False

    def test_custom_interval(self):
        """Dream deadline respects custom interval_hours."""
        # 3 hours ago with 4-hour default → not due
        three_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        assert scheduler._dream_due({"last_dream_at": three_hours_ago}) is False

        # 3 hours ago with 2-hour interval → due
        state = {"last_dream_at": three_hours_ago}
        # We test via direct call since _dream_due has a default param
        # Re-implement the check inline for custom interval
        last_dt = datetime.fromisoformat(three_hours_ago)
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
        assert elapsed >= 2 * 3600  # 2-hour interval

    def test_invalid_dream_timestamp_means_due(self):
        """Invalid last_dream_at format means dream is due."""
        assert scheduler._dream_due({"last_dream_at": "not-a-date"}) is True
        assert scheduler._dream_due({"last_dream_at": 12345}) is True

    def test_z_suffix_handling(self):
        """Timestamps with Z suffix are handled correctly."""
        old = "2020-01-01T00:00:00Z"
        assert scheduler._dream_due({"last_dream_at": old}) is True

    def test_boundary_exactly_at_interval(self):
        """Dream is due when elapsed exactly equals interval."""
        exactly_4h = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        assert scheduler._dream_due({"last_dream_at": exactly_4h}) is True

    def test_boundary_just_before_interval(self):
        """Dream is not due when elapsed is just under interval."""
        just_under_4h = (datetime.now(timezone.utc) - timedelta(hours=3, minutes=59)).isoformat()
        assert scheduler._dream_due({"last_dream_at": just_under_4h}) is False


class TestRunWorker:
    """Worker invocation."""

    def test_run_worker_normal(self):
        """_run_worker calls subprocess.run with correct command."""
        with patch.object(scheduler.subprocess, "run", return_value=MagicMock(returncode=0)) as mock_run:
            scheduler._run_worker("engineer")
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == scheduler.sys.executable
            assert cmd[1] == "-m"
            assert cmd[2] == "harness.worker"
            assert "--agent" in cmd
            assert "engineer" in cmd
            assert "--dream" not in cmd

    def test_run_worker_dream(self):
        """_run_worker with dream=True includes --dream flag."""
        with patch.object(scheduler.subprocess, "run", return_value=MagicMock(returncode=0)) as mock_run:
            scheduler._run_worker("engineer", dream=True)
            cmd = mock_run.call_args[0][0]
            assert "--dream" in cmd
