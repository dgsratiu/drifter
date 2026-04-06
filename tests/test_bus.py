"""Integration tests for bus operations.

Each test creates a temporary drifter project, initializes the database,
and runs `drifter` CLI commands to verify bus behavior.
"""

import json
import os
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

DRIFTER_BIN = os.environ.get("DRIFTER_BIN") or os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "rust", "target", "debug", "drifter"
))


def _run(cmd, cwd, check=True):
    """Run a shell command in the given directory."""
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Test User"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "Test User"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    return subprocess.run(
        cmd, shell=True, cwd=cwd, capture_output=True, text=True, check=check, env=env
    )


def _drifter(args, cwd, check=True):
    """Run `drifter` CLI with given args in the given directory."""
    db_path = os.path.join(cwd, "drifter.db")
    cmd = f'{DRIFTER_BIN} --db "{db_path}" {args}'
    return subprocess.run(
        cmd, shell=True, cwd=cwd, capture_output=True, text=True, check=check
    )


def _init_drifter_project(root):
    """Create a minimal drifter project with initialized database."""
    # constitution.md (immutable)
    (root / "constitution.md").write_text("# Constitution\n")

    # drifter.toml (immutable)
    (root / "drifter.toml").write_text('api_key = "test"\n')

    # rust/ directory with a minimal Cargo project
    rust_src = root / "rust" / "src"
    rust_src.mkdir(parents=True)
    (root / "rust" / "Cargo.toml").write_text(
        textwrap.dedent("""\
            [package]
            name = "drifter"
            version = "0.1.0"
            edition = "2021"

            [dependencies]
            anyhow = "1"
            tokio = { version = "1", features = ["full"] }
        """)
    )
    (rust_src / "main.rs").write_text(
        'fn main() { println!("hello"); }\n'
    )

    # rust/migrations/ with one migration
    migrations = root / "rust" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "20240101000000_init.sql").write_text(
        "CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY);\n"
    )

    # docs/prd.md (project root marker)
    (root / "docs").mkdir()
    (root / "docs" / "prd.md").write_text("# PRD\n")

    # schema.sql (project root marker)
    (root / "schema.sql").write_text("-- schema\n")

    # tests/ directory (empty)
    (root / "tests").mkdir()
    (root / "tests" / ".gitkeep").write_text("")

    # agents/ directory
    (root / "agents").mkdir()

    # Initialize the database
    _drifter("init", str(root))

    # Register the engineer agent (immortal)
    _drifter(
        'birth engineer --soul /dev/null --model "openrouter/auto" --immortal',
        str(root),
    )


# ---------------------------------------------------------------------------
# Test: post and read messages
# ---------------------------------------------------------------------------

class TestPostRead:
    """Verify posting and reading messages on the bus."""

    def test_post_to_existing_channel(self, tmp_path):
        """Posting to an existing channel should succeed."""
        _init_drifter_project(tmp_path)

        result = _drifter(
            'post engineering "hello world" --agent engineer', str(tmp_path)
        )
        assert result.returncode == 0
        assert "posted to #engineering" in result.stdout

    def test_post_creates_channel(self, tmp_path):
        """Posting to a non-existent channel should auto-create it."""
        _init_drifter_project(tmp_path)

        result = _drifter(
            'post meetings "meeting notes" --agent engineer', str(tmp_path)
        )
        assert result.returncode == 0

        # Verify channel exists
        result = _drifter("channels --json", str(tmp_path))
        assert result.returncode == 0
        channels = json.loads(result.stdout)
        names = [ch["name"] for ch in channels]
        assert "meetings" in names

    def test_read_messages(self, tmp_path):
        """Reading messages should return posted content."""
        _init_drifter_project(tmp_path)

        _drifter('post engineering "first message" --agent engineer --type system', str(tmp_path))
        _drifter('post engineering "second message" --agent engineer --type system', str(tmp_path))

        result = _drifter("read engineering --json", str(tmp_path))
        assert result.returncode == 0
        messages = json.loads(result.stdout)
        assert len(messages) == 2
        assert messages[0]["content"] == "first message"
        assert messages[1]["content"] == "second message"

    def test_read_with_since(self, tmp_path):
        """Reading with --since should return messages after that seq."""
        _init_drifter_project(tmp_path)

        _drifter('post engineering "msg 1" --agent engineer --type system', str(tmp_path))
        _drifter('post engineering "msg 2" --agent engineer --type system', str(tmp_path))
        _drifter('post engineering "msg 3" --agent engineer --type system', str(tmp_path))

        # Get all messages to find the seq of msg 2
        result = _drifter("read engineering --json", str(tmp_path))
        messages = json.loads(result.stdout)
        msg2_seq = next(m["seq"] for m in messages if m["content"] == "msg 2")

        # Read since msg 2's seq (should get msg 3 only, since is exclusive)
        result = _drifter(f"read engineering --since {msg2_seq} --json", str(tmp_path))
        assert result.returncode == 0
        messages = json.loads(result.stdout)
        assert len(messages) == 1
        assert messages[0]["content"] == "msg 3"

    def test_read_with_limit(self, tmp_path):
        """Reading with --limit should return at most N messages."""
        _init_drifter_project(tmp_path)

        for i in range(5):
            _drifter(f'post engineering "msg {i}" --agent engineer --type system', str(tmp_path))

        result = _drifter("read engineering --limit 2 --json", str(tmp_path))
        assert result.returncode == 0
        messages = json.loads(result.stdout)
        assert len(messages) == 2

    def test_read_nonexistent_channel_fails(self, tmp_path):
        """Reading from a channel that doesn't exist should fail."""
        _init_drifter_project(tmp_path)

        result = _drifter("read nonexistent --json", str(tmp_path), check=False)
        assert result.returncode == 1

    def test_post_with_metadata(self, tmp_path):
        """Posting with metadata should include it in the message."""
        _init_drifter_project(tmp_path)

        result = _drifter(
            'post engineering "test" --agent engineer --metadata \'{"trigger":"tension:gap"}\'',
            str(tmp_path),
        )
        assert result.returncode == 0

        result = _drifter("read engineering --json", str(tmp_path))
        messages = json.loads(result.stdout)
        meta = json.loads(messages[0]["metadata"])
        assert meta["trigger"] == "tension:gap"
        # Auto-populated fields
        assert "model" in meta
        assert "timestamp" in meta

    def test_post_with_type(self, tmp_path):
        """Posting with --type should set the message type."""
        _init_drifter_project(tmp_path)

        result = _drifter(
            'post engineering "system msg" --agent engineer --type system', str(tmp_path)
        )
        assert result.returncode == 0

        result = _drifter("read engineering --json", str(tmp_path))
        messages = json.loads(result.stdout)
        assert messages[0]["type"] == "system"


# ---------------------------------------------------------------------------
# Test: inbox/ack cycle
# ---------------------------------------------------------------------------

class TestInboxAck:
    """Verify inbox routing and acknowledgment."""

    def test_inbox_empty_for_new_agent(self, tmp_path):
        """A new agent with no messages should have an empty inbox."""
        _init_drifter_project(tmp_path)

        result = _drifter("inbox engineer --json", str(tmp_path))
        assert result.returncode == 0
        items = json.loads(result.stdout)
        assert len(items) == 0

    def test_watcher_receives_inbox(self, tmp_path):
        """A watcher should receive inbox entries for channel posts."""
        _init_drifter_project(tmp_path)

        _drifter(
            'birth analyst --soul /dev/null --model "openrouter/auto"', str(tmp_path)
        )
        _drifter("watch analyst engineering", str(tmp_path))

        # Engineer posts (not the watcher, since poster is excluded from inbox)
        _drifter(
            'post engineering "analyst update" --agent engineer --type system', str(tmp_path)
        )

        result = _drifter("inbox analyst --json", str(tmp_path))
        assert result.returncode == 0
        items = json.loads(result.stdout)
        assert len(items) == 1
        assert items[0]["content"] == "analyst update"
        assert items[0]["trigger"] == "watch"

    def test_ack_removes_from_inbox(self, tmp_path):
        """Acknowledging an inbox entry should remove it from the inbox."""
        _init_drifter_project(tmp_path)

        _drifter(
            'birth analyst --soul /dev/null --model "openrouter/auto"', str(tmp_path)
        )
        _drifter("watch analyst engineering", str(tmp_path))

        # Engineer posts so analyst (the watcher) gets an inbox entry
        _drifter(
            'post engineering "to ack" --agent engineer --type system', str(tmp_path)
        )

        # Verify inbox has entry
        result = _drifter("inbox analyst --json", str(tmp_path))
        items = json.loads(result.stdout)
        assert len(items) == 1
        inbox_id = items[0]["id"]

        # Ack it
        result = _drifter(f"ack {inbox_id}", str(tmp_path))
        assert result.returncode == 0

        # Verify inbox is empty
        result = _drifter("inbox analyst --json", str(tmp_path))
        items = json.loads(result.stdout)
        assert len(items) == 0

    def test_poster_not_inbox(self, tmp_path):
        """The poster should not receive an inbox entry for their own post."""
        _init_drifter_project(tmp_path)

        _drifter("watch engineer engineering", str(tmp_path))

        _drifter(
            'post engineering "my own post" --agent engineer --type system', str(tmp_path)
        )

        result = _drifter("inbox engineer --json", str(tmp_path))
        items = json.loads(result.stdout)
        assert len(items) == 0


# ---------------------------------------------------------------------------
# Test: watcher routing
# ---------------------------------------------------------------------------

class TestWatcherRouting:
    """Verify watcher-based message routing."""

    def test_watcher_receives_on_post(self, tmp_path):
        """A watcher should receive inbox entries when messages are posted."""
        _init_drifter_project(tmp_path)

        _drifter(
            'birth analyst --soul /dev/null --model "openrouter/auto"', str(tmp_path)
        )
        _drifter("watch analyst engineering", str(tmp_path))

        # Engineer posts so analyst gets inboxed
        _drifter(
            'post engineering "watched" --agent engineer --type system', str(tmp_path)
        )

        result = _drifter("inbox analyst --json", str(tmp_path))
        items = json.loads(result.stdout)
        assert len(items) == 1
        assert items[0]["channel"] == "engineering"

    def test_unwatch_stops_routing(self, tmp_path):
        """Unwatching should stop inbox routing."""
        _init_drifter_project(tmp_path)

        _drifter(
            'birth analyst --soul /dev/null --model "openrouter/auto"', str(tmp_path)
        )
        _drifter("watch analyst engineering", str(tmp_path))
        _drifter("unwatch analyst engineering", str(tmp_path))

        _drifter(
            'post engineering "unwatched" --agent analyst --type system', str(tmp_path)
        )

        result = _drifter("inbox analyst --json", str(tmp_path))
        items = json.loads(result.stdout)
        assert len(items) == 0

    def test_multiple_watchers(self, tmp_path):
        """Multiple watchers should all receive inbox entries."""
        _init_drifter_project(tmp_path)

        _drifter(
            'birth analyst --soul /dev/null --model "openrouter/auto"', str(tmp_path)
        )
        _drifter(
            'birth digest --soul /dev/null --model "openrouter/auto"', str(tmp_path)
        )
        _drifter("watch analyst engineering", str(tmp_path))
        _drifter("watch digest engineering", str(tmp_path))

        _drifter(
            'post engineering "multi" --agent analyst --type system', str(tmp_path)
        )

        for agent in ["analyst", "digest"]:
            result = _drifter(f"inbox {agent} --json", str(tmp_path))
            items = json.loads(result.stdout)
            assert len(items) == 1


# ---------------------------------------------------------------------------
# Test: wake file creation
# ---------------------------------------------------------------------------

class TestWakeFiles:
    """Verify wake file creation on message routing."""

    def test_wake_file_created_for_watcher(self, tmp_path):
        """A wake file should be created for a watcher who receives a message."""
        _init_drifter_project(tmp_path)

        _drifter(
            'birth analyst --soul /dev/null --model "openrouter/auto"', str(tmp_path)
        )
        _drifter("watch analyst engineering", str(tmp_path))

        wake_path = tmp_path / "agents" / "analyst" / ".wake"
        assert not wake_path.exists()

        # Engineer posts so analyst gets wake file
        _drifter(
            'post engineering "wake up" --agent engineer --type system', str(tmp_path)
        )

        assert wake_path.exists()

    def test_wake_file_not_created_for_poster(self, tmp_path):
        """The poster should not get a wake file for their own post."""
        _init_drifter_project(tmp_path)

        _drifter("watch engineer engineering", str(tmp_path))

        wake_path = tmp_path / "agents" / "engineer" / ".wake"

        _drifter(
            'post engineering "my post" --agent engineer --type system', str(tmp_path)
        )

        assert not wake_path.exists()


# ---------------------------------------------------------------------------
# Test: proposals
# ---------------------------------------------------------------------------

class TestProposals:
    """Verify proposal creation, approval, and rejection."""

    def test_create_proposal(self, tmp_path):
        """Creating a proposal should succeed."""
        _init_drifter_project(tmp_path)

        soul_file = tmp_path / "soul.md"
        soul_file.write_text("# analyst\n\ni am analyst.\n")

        result = _drifter(
            f'propose analyst --hypothesis "analysts analyze" --soul-file {soul_file}',
            str(tmp_path),
        )
        assert result.returncode == 0
        assert "Proposal created" in result.stdout

    def test_list_proposals(self, tmp_path):
        """Listing proposals should show created proposals."""
        _init_drifter_project(tmp_path)

        soul_file = tmp_path / "soul.md"
        soul_file.write_text("# analyst\n\ni am analyst.\n")

        _drifter(
            f'propose analyst --hypothesis "analysts analyze" --soul-file {soul_file}',
            str(tmp_path),
        )

        result = _drifter("proposals --json", str(tmp_path))
        assert result.returncode == 0
        proposals = json.loads(result.stdout)
        assert len(proposals) == 1
        assert proposals[0]["agent_name"] == "analyst"
        assert proposals[0]["status"] == "pending"

    def test_approve_proposal(self, tmp_path):
        """Approving a proposal should succeed and trigger birth."""
        _init_drifter_project(tmp_path)

        soul_file = tmp_path / "soul.md"
        soul_file.write_text("# analyst\n\ni am analyst.\n")

        result = _drifter(
            f'propose analyst --hypothesis "analysts analyze" --soul-file {soul_file}',
            str(tmp_path),
        )
        assert result.returncode == 0

        # Get proposal ID
        result = _drifter("proposals --json", str(tmp_path))
        proposals = json.loads(result.stdout)
        proposal_id = proposals[0]["id"]

        result = _drifter(f"approve {proposal_id}", str(tmp_path))
        assert result.returncode == 0
        assert "Approved" in result.stdout

        # Verify agent was created
        result = _drifter("agents --json", str(tmp_path))
        agents = json.loads(result.stdout)
        names = [a["name"] for a in agents]
        assert "analyst" in names

    def test_reject_proposal(self, tmp_path):
        """Rejecting a proposal should succeed."""
        _init_drifter_project(tmp_path)

        soul_file = tmp_path / "soul.md"
        soul_file.write_text("# analyst\n\ni am analyst.\n")

        _drifter(
            f'propose analyst --hypothesis "analysts analyze" --soul-file {soul_file}',
            str(tmp_path),
        )

        result = _drifter("proposals --json", str(tmp_path))
        proposals = json.loads(result.stdout)
        proposal_id = proposals[0]["id"]

        result = _drifter(f"reject {proposal_id}", str(tmp_path))
        assert result.returncode == 0

        result = _drifter("proposals --json", str(tmp_path))
        proposals = json.loads(result.stdout)
        assert proposals[0]["status"] == "rejected"


# ---------------------------------------------------------------------------
# Test: rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    """Verify rate limiting on message posting."""

    def test_rate_limit_enforced(self, tmp_path):
        """Posting too many messages quickly should be rate limited."""
        _init_drifter_project(tmp_path)

        # Birth already uses 2 posts (system type doesn't count)
        # Post 2 regular messages - should succeed
        result1 = _drifter('post engineering "msg 1" --agent engineer', str(tmp_path))
        assert result1.returncode == 0

        # 2nd post should fail (birth used 2 posts already)
        result2 = _drifter('post engineering "msg 2" --agent engineer', str(tmp_path), check=False)
        assert result2.returncode == 1
        assert "rate limit" in result2.stderr.lower()

    def test_system_messages_bypass_rate_limit(self, tmp_path):
        """System messages should bypass rate limiting."""
        _init_drifter_project(tmp_path)

        # Post 5 system messages - all should succeed
        for i in range(5):
            result = _drifter(
                f'post engineering "system {i}" --agent engineer --type system',
                str(tmp_path),
            )
            assert result.returncode == 0


# ---------------------------------------------------------------------------
# Test: metrics
# ---------------------------------------------------------------------------

class TestMetrics:
    """Verify metrics recording and retrieval."""

    def test_metrics_command_succeeds(self, tmp_path):
        """The metrics command should succeed even with no data."""
        _init_drifter_project(tmp_path)

        result = _drifter("metrics engineer --json", str(tmp_path))
        assert result.returncode == 0
        metrics = json.loads(result.stdout)
        assert isinstance(metrics, list)


# ---------------------------------------------------------------------------
# Test: per-agent working directories
# ---------------------------------------------------------------------------

class TestWorkingDirectories:
    """Verify per-agent working directory creation and DB storage."""

    def test_birth_creates_worktree(self, tmp_path):
        """Birth should create a git worktree for the new agent."""
        _init_drifter_project(tmp_path)

        # Initialize git repo (birth needs it for worktree creation)
        _run("git init", str(tmp_path))
        _run("git add -A", str(tmp_path))
        _run("git commit -m 'init'", str(tmp_path))

        _drifter(
            'birth analyst --soul /dev/null --model "openrouter/auto"', str(tmp_path)
        )

        worktree = tmp_path / "agents" / "analyst" / "worktree"
        assert worktree.exists()
        assert (worktree / "opencode.json").exists()

    def test_birth_stores_working_dir_in_db(self, tmp_path):
        """Birth should store the relative working_dir in the agents table."""
        _init_drifter_project(tmp_path)

        _run("git init", str(tmp_path))
        _run("git add -A", str(tmp_path))
        _run("git commit -m 'init'", str(tmp_path))

        _drifter(
            'birth analyst --soul /dev/null --model "openrouter/auto"', str(tmp_path)
        )

        result = _drifter("agents --json", str(tmp_path))
        agents = json.loads(result.stdout)
        analyst = next(a for a in agents if a["name"] == "analyst")
        assert analyst["working_dir"] == "agents/analyst/worktree"

    def test_engineer_has_no_working_dir(self, tmp_path):
        """Engineer (born before worktree support) should have null working_dir."""
        _init_drifter_project(tmp_path)

        result = _drifter("agents --json", str(tmp_path))
        agents = json.loads(result.stdout)
        engineer = next(a for a in agents if a["name"] == "engineer")
        assert engineer["working_dir"] is None
