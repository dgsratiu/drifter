"""Tests for the transcripts gateway.

Tests cover:
- file hashing (SHA-256)
- state loading/saving (posted file tracking)
- directory scanning (finding new .md files)
- transcript posting (drifter CLI invocation)
- main() CLI integration (dry-run, posting, no files)
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from gateways import transcripts


# ---------------------------------------------------------------------------
# Test: file_hash
# ---------------------------------------------------------------------------

class TestFileHash:
    """Verify SHA-256 hashing of file content."""

    def test_hash_is_deterministic(self, tmp_path):
        """Same content should produce the same hash."""
        f = tmp_path / "test.md"
        f.write_text("hello world")
        h1 = transcripts.file_hash(f)
        h2 = transcripts.file_hash(f)
        assert h1 == h2

    def test_hash_differs_for_different_content(self, tmp_path):
        """Different content should produce different hashes."""
        f1 = tmp_path / "a.md"
        f2 = tmp_path / "b.md"
        f1.write_text("hello")
        f2.write_text("world")
        assert transcripts.file_hash(f1) != transcripts.file_hash(f2)

    def test_hash_is_hex_string(self, tmp_path):
        """Hash should be a valid hex string (SHA-256 = 64 chars)."""
        f = tmp_path / "test.md"
        f.write_text("test")
        h = transcripts.file_hash(f)
        assert len(h) == 64
        int(h, 16)  # raises if not valid hex


# ---------------------------------------------------------------------------
# Test: load_state / save_state
# ---------------------------------------------------------------------------

class TestStatePersistence:
    """Verify state file loading and saving."""

    def test_load_state_empty_file(self, tmp_path):
        """Non-existent state file should return empty set."""
        state_path = tmp_path / "missing.state"
        assert transcripts.load_state(state_path) == set()

    def test_load_state_valid(self, tmp_path):
        """Valid state file should return the posted set."""
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"posted": ["abc", "def"]}))
        result = transcripts.load_state(state_path)
        assert result == {"abc", "def"}

    def test_load_state_corrupt_json(self, tmp_path):
        """Corrupt JSON should return empty set."""
        state_path = tmp_path / "state.json"
        state_path.write_text("not json")
        assert transcripts.load_state(state_path) == set()

    def test_load_state_missing_key(self, tmp_path):
        """JSON without 'posted' key should return empty set."""
        state_path = tmp_path / "state.json"
        state_path.write_text('{"other": true}')
        assert transcripts.load_state(state_path) == set()

    def test_save_state_creates_parent_dirs(self, tmp_path):
        """save_state should create parent directories if needed."""
        state_path = tmp_path / "sub" / "dir" / "state.json"
        transcripts.save_state(state_path, {"a", "b"})
        assert state_path.exists()

    def test_save_and_reload_roundtrip(self, tmp_path):
        """Saved state should be reloadable."""
        state_path = tmp_path / "state.json"
        original = {"hash1", "hash2", "hash3"}
        transcripts.save_state(state_path, original)
        reloaded = transcripts.load_state(state_path)
        assert reloaded == original

    def test_save_state_sorts_posted(self, tmp_path):
        """Posted list should be sorted in the saved file."""
        state_path = tmp_path / "state.json"
        transcripts.save_state(state_path, {"z", "a", "m"})
        data = json.loads(state_path.read_text())
        assert data["posted"] == ["a", "m", "z"]


# ---------------------------------------------------------------------------
# Test: scan_directory
# ---------------------------------------------------------------------------

class TestScanDirectory:
    """Verify directory scanning for new .md files."""

    def test_no_directory(self, tmp_path):
        """Non-existent directory should return empty list."""
        missing = tmp_path / "does_not_exist"
        result = transcripts.scan_directory(missing, set())
        assert result == []

    def test_no_md_files(self, tmp_path):
        """Directory with no .md files should return empty list."""
        (tmp_path / "notes.txt").write_text("hi")
        result = transcripts.scan_directory(tmp_path, set())
        assert result == []

    def test_finds_new_md_files(self, tmp_path):
        """Should find .md files not yet posted."""
        f = tmp_path / "meeting.md"
        f.write_text("transcript content")
        result = transcripts.scan_directory(tmp_path, set())
        assert len(result) == 1
        assert result[0][0] == f
        assert result[0][1] == transcripts.file_hash(f)

    def test_skips_already_posted(self, tmp_path):
        """Should skip files whose hash is in the posted set."""
        f = tmp_path / "meeting.md"
        f.write_text("transcript content")
        fh = transcripts.file_hash(f)
        result = transcripts.scan_directory(tmp_path, {fh})
        assert result == []

    def test_skips_hidden_files(self, tmp_path):
        """Should skip files starting with a dot."""
        (tmp_path / ".hidden.md").write_text("secret")
        (tmp_path / "visible.md").write_text("public")
        result = transcripts.scan_directory(tmp_path, set())
        assert len(result) == 1
        assert result[0][0].name == "visible.md"

    def test_multiple_new_files(self, tmp_path):
        """Should find multiple new .md files."""
        (tmp_path / "a.md").write_text("aaa")
        (tmp_path / "b.md").write_text("bbb")
        (tmp_path / "c.md").write_text("ccc")
        result = transcripts.scan_directory(tmp_path, set())
        assert len(result) == 3

    def test_partial_posted_set(self, tmp_path):
        """Should only return files not in the posted set."""
        fa = tmp_path / "a.md"
        fb = tmp_path / "b.md"
        fa.write_text("aaa")
        fb.write_text("bbb")
        hash_a = transcripts.file_hash(fa)
        result = transcripts.scan_directory(tmp_path, {hash_a})
        assert len(result) == 1
        assert result[0][0] == fb


# ---------------------------------------------------------------------------
# Test: post_transcript
# ---------------------------------------------------------------------------

class TestPostTranscript:
    """Verify transcript posting via drifter CLI."""

    def test_post_calls_drifter(self, tmp_path):
        """post_transcript should invoke run_drifter with correct args."""
        with patch.object(transcripts, "run_drifter") as mock_run:
            transcripts.post_transcript(tmp_path, "meeting.md", "content here", "meetings")
            mock_run.assert_called_once()
            args = mock_run.call_args[0]
            assert args[0] == tmp_path
            assert args[1] == "post"
            assert args[2] == "meetings"
            assert "MEETING TRANSCRIPT: meeting.md" in args[3]
            assert "--agent" in mock_run.call_args[0]

    def test_post_truncates_long_content(self, tmp_path):
        """Content over 2000 chars should be truncated."""
        long_content = "x" * 3000
        with patch.object(transcripts, "run_drifter") as mock_run:
            transcripts.post_transcript(tmp_path, "long.md", long_content, "meetings")
            msg = mock_run.call_args[0][3]
            assert len(msg) < len(long_content)

    def test_post_handles_drifter_failure(self, tmp_path, capsys):
        """Failed drifter call should print error to stderr, not raise."""
        with patch.object(
            transcripts, "run_drifter",
            side_effect=subprocess.CalledProcessError(1, "drifter", stderr="db locked")
        ):
            transcripts.post_transcript(tmp_path, "meeting.md", "content", "meetings")
            captured = capsys.readouterr()
            assert "failed to post" in captured.err


# ---------------------------------------------------------------------------
# Test: run_drifter
# ---------------------------------------------------------------------------

class TestRunDrifter:
    """Verify drifter CLI invocation."""

    def test_run_drifter_calls_subprocess(self, tmp_path):
        """run_drifter should call subprocess.run with correct command."""
        with patch.object(transcripts.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(stdout="output\n", stderr="")
            transcripts.run_drifter(tmp_path, "post", "engineering", "hello")
            cmd = mock_run.call_args[0][0]
            assert "post" in cmd
            assert "engineering" in cmd
            assert "hello" in cmd
            assert "--db" in cmd

    def test_run_drifter_uses_env_var(self, tmp_path):
        """run_drifter should respect DRIFTER_BIN env var."""
        with patch.object(transcripts.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="")
            with patch.dict(transcripts.os.environ, {"DRIFTER_BIN": "/custom/drifter"}):
                transcripts.run_drifter(tmp_path, "channels")
                cmd = mock_run.call_args[0][0]
                assert cmd[0] == "/custom/drifter"


# ---------------------------------------------------------------------------
# Test: main() CLI
# ---------------------------------------------------------------------------

class TestMainCLI:
    """Verify main() CLI behavior."""

    def test_no_files_returns_zero(self, tmp_path):
        """Empty transcript directory should return 0."""
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        with patch.object(transcripts, "main") as mock_main:
            mock_main.return_value = 0
            assert transcripts.main() == 0

    def test_dry_run_lists_files(self, tmp_path, capsys):
        """--dry-run should list files without posting."""
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        (transcript_dir / "meeting.md").write_text("notes")

        # Use a temp state path so the real project's state file doesn't interfere
        state_path = tmp_path / ".drifter" / "transcripts-gateway.state"
        with patch.object(transcripts.sys, "argv", ["transcripts.py", "--directory", str(transcript_dir), "--dry-run"]):
            with patch.object(transcripts, "load_state", return_value=set()):
                result = transcripts.main()
                assert result == 0
                captured = capsys.readouterr()
                assert "meeting.md" in captured.out

    def test_dry_run_no_post(self, tmp_path):
        """--dry-run should not call run_drifter."""
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        (transcript_dir / "meeting.md").write_text("notes")

        with patch.object(transcripts.sys, "argv", ["transcripts.py", "--directory", str(transcript_dir), "--dry-run"]):
            with patch.object(transcripts, "run_drifter") as mock_run:
                transcripts.main()
                mock_run.assert_not_called()

    def test_posts_new_files(self, tmp_path, capsys):
        """Should post new files and update state."""
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        (transcript_dir / "meeting.md").write_text("meeting notes here")

        state_dir = tmp_path / ".drifter"
        state_dir.mkdir()

        with patch.object(transcripts.sys, "argv", ["transcripts.py", "--directory", str(transcript_dir)]):
            with patch.object(transcripts, "load_state", return_value=set()):
                with patch.object(transcripts, "run_drifter") as mock_run:
                    mock_run.return_value = ""
                    result = transcripts.main()
                    assert result == 0
                    mock_run.assert_called_once()

    def test_custom_channel(self, tmp_path):
        """--channel flag should override default channel."""
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        (transcript_dir / "meeting.md").write_text("notes")

        with patch.object(transcripts.sys, "argv", [
            "transcripts.py",
            "--directory", str(transcript_dir),
            "--channel", "archive",
        ]):
            with patch.object(transcripts, "load_state", return_value=set()):
                with patch.object(transcripts, "run_drifter") as mock_run:
                    mock_run.return_value = ""
                    transcripts.main()
                    call_args = mock_run.call_args[0]
                    assert call_args[2] == "archive"

    def test_skips_already_posted(self, tmp_path):
        """Should not re-post files already in state."""
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        f = transcript_dir / "meeting.md"
        f.write_text("notes")
        fh = transcripts.file_hash(f)

        state_path = tmp_path / ".drifter" / "transcripts-gateway.state"
        state_path.parent.mkdir(parents=True)
        transcripts.save_state(state_path, {fh})

        with patch.object(transcripts.sys, "argv", ["transcripts.py", "--directory", str(transcript_dir)]):
            with patch.object(transcripts, "run_drifter") as mock_run:
                transcripts.main()
                mock_run.assert_not_called()
