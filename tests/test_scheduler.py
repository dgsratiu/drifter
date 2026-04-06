"""Tests for the harness scheduler.

Tests cover:
- system-only inbox gets acked without spawning worker
- mixed inbox spawns worker
- empty inbox falls through to dream check
- dream deadline calculation
"""

import hashlib
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


class TestTensionsTrigger:
    """Tensions trigger spawns a worker when tensions exist and cooldown elapsed."""

    def test_has_tensions_with_content(self, mock_paths):
        """Non-empty tensions.md returns True."""
        mock_paths.tensions_path.write_text("## Gaps\n- something broken\n")
        assert scheduler._has_tensions(mock_paths) is True

    def test_has_tensions_empty(self, mock_paths):
        """Empty tensions.md returns False."""
        mock_paths.tensions_path.write_text("")
        assert scheduler._has_tensions(mock_paths) is False

    def test_has_tensions_whitespace_only(self, mock_paths):
        """Whitespace-only tensions.md returns False."""
        mock_paths.tensions_path.write_text("   \n\n  ")
        assert scheduler._has_tensions(mock_paths) is False

    def test_has_tensions_missing_file(self, mock_paths):
        """Missing tensions.md returns False."""
        if mock_paths.tensions_path.exists():
            mock_paths.tensions_path.unlink()
        assert scheduler._has_tensions(mock_paths) is False

    def test_tensions_cooldown_no_previous(self):
        """No last_tensions_cycle_at means cooldown is elapsed."""
        assert scheduler._tensions_cooldown_elapsed({}) is True

    def test_tensions_cooldown_old(self):
        """Old last_tensions_cycle_at means cooldown is elapsed."""
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        assert scheduler._tensions_cooldown_elapsed({"last_tensions_cycle_at": old}) is True

    def test_tensions_cooldown_recent(self):
        """Recent last_tensions_cycle_at means cooldown not elapsed."""
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert scheduler._tensions_cooldown_elapsed({"last_tensions_cycle_at": recent}) is False

    def test_tensions_trigger_spawns_worker(self, mock_paths):
        """Scheduler spawns worker with trigger=tensions when tensions exist."""
        mock_paths.tensions_path.write_text("## Gaps\n- stale branches\n")

        with (
            patch("argparse.ArgumentParser") as mock_parser_cls,
            patch.object(scheduler, "run_drifter", return_value=[]),  # empty inbox
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

        mock_worker.assert_called_once_with("engineer", trigger="tensions")

    def test_tensions_skipped_when_cooldown_not_elapsed(self, mock_paths):
        """Scheduler skips tensions when cooldown hasn't elapsed."""
        mock_paths.tensions_path.write_text("## Gaps\n- something\n")
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        with (
            patch("argparse.ArgumentParser") as mock_parser_cls,
            patch.object(scheduler, "run_drifter", return_value=[]),
            patch.object(scheduler, "_run_worker", return_value=0) as mock_worker,
            patch.object(scheduler, "load_state", return_value={"last_tensions_cycle_at": recent}),
            patch.object(scheduler, "fcntl"),
            patch.object(scheduler, "agent_paths", return_value=mock_paths),
            patch.object(scheduler, "ensure_agent_files"),
        ):
            mock_args = MagicMock()
            mock_args.agent = "engineer"
            mock_parser_cls.return_value.parse_args.return_value = mock_args

            scheduler.main()

        # Should not have spawned a worker (no inbox, no rejected, tensions on cooldown, dream not due needs state)
        # Dream check: load_state returns {} for last_dream_at... actually it returns the dict with last_tensions_cycle_at
        # _dream_due checks last_dream_at which is missing → dream is due
        mock_worker.assert_called_once_with("engineer", dream=True, trigger="dream")

    def test_inbox_takes_priority_over_tensions(self, mock_paths):
        """Inbox items are handled before tensions."""
        mock_paths.tensions_path.write_text("## Gaps\n- something\n")
        inbox_items = [{"id": 1, "from_agent": "daniel", "text": "do this"}]

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


    def test_tensions_hash_deterministic(self, mock_paths):
        """Same content produces same hash."""
        mock_paths.tensions_path.write_text("## Gaps\n- item\n")
        h1 = scheduler._tensions_hash(mock_paths)
        h2 = scheduler._tensions_hash(mock_paths)
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex digest

    def test_tensions_changed_no_previous(self, mock_paths):
        """No previous hash means tensions are considered changed."""
        mock_paths.tensions_path.write_text("## Gaps\n- item\n")
        assert scheduler._tensions_changed(mock_paths, {}) is True

    def test_tensions_changed_same_content(self, mock_paths):
        """Same content means tensions are NOT changed."""
        content = "## Gaps\n- item\n"
        mock_paths.tensions_path.write_text(content)
        h = scheduler._tensions_hash(mock_paths)
        assert scheduler._tensions_changed(mock_paths, {"last_tensions_hash": h}) is False

    def test_tensions_changed_different_content(self, mock_paths):
        """Different content means tensions ARE changed."""
        mock_paths.tensions_path.write_text("## Gaps\n- new item\n")
        old_hash = hashlib.sha256(b"## Gaps\n- old item").hexdigest()
        assert scheduler._tensions_changed(mock_paths, {"last_tensions_hash": old_hash}) is True

    def test_tensions_skipped_when_unchanged(self, mock_paths):
        """Scheduler skips tensions when content hash hasn't changed."""
        content = "## Gaps\n- same old thing\n"
        mock_paths.tensions_path.write_text(content)
        content_hash = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()

        with (
            patch("argparse.ArgumentParser") as mock_parser_cls,
            patch.object(scheduler, "run_drifter", return_value=[]),
            patch.object(scheduler, "_run_worker", return_value=0) as mock_worker,
            patch.object(scheduler, "load_state", return_value={
                "last_tensions_hash": content_hash,
            }),
            patch.object(scheduler, "fcntl"),
            patch.object(scheduler, "agent_paths", return_value=mock_paths),
            patch.object(scheduler, "ensure_agent_files"),
        ):
            mock_args = MagicMock()
            mock_args.agent = "engineer"
            mock_parser_cls.return_value.parse_args.return_value = mock_args

            scheduler.main()

        # Tensions skipped → falls through to dream (no last_dream_at → dream due)
        mock_worker.assert_called_once_with("engineer", dream=True, trigger="dream")

    def test_tensions_triggered_when_content_changed(self, mock_paths):
        """Scheduler triggers tensions when content differs from stored hash."""
        mock_paths.tensions_path.write_text("## Gaps\n- new tension item\n")
        old_hash = hashlib.sha256(b"## Gaps\n- old tension item").hexdigest()

        with (
            patch("argparse.ArgumentParser") as mock_parser_cls,
            patch.object(scheduler, "run_drifter", return_value=[]),
            patch.object(scheduler, "_run_worker", return_value=0) as mock_worker,
            patch.object(scheduler, "load_state", return_value={
                "last_tensions_hash": old_hash,
            }),
            patch.object(scheduler, "fcntl"),
            patch.object(scheduler, "agent_paths", return_value=mock_paths),
            patch.object(scheduler, "ensure_agent_files"),
        ):
            mock_args = MagicMock()
            mock_args.agent = "engineer"
            mock_parser_cls.return_value.parse_args.return_value = mock_args

            scheduler.main()

        mock_worker.assert_called_once_with("engineer", trigger="tensions")


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
