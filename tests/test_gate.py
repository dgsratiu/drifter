"""Integration tests for the quality gate.

Each test creates a temporary git repo that mimics a drifter project,
stages specific changes, and runs `drifter gate` to verify pass/fail behavior.
"""

import os
import subprocess
import tempfile
import textwrap
import shutil
import pytest

DRIFTER_BIN = os.environ.get("DRIFTER_BIN") or os.path.join(
    os.path.dirname(__file__), "..", "rust", "target", "debug", "drifter"
)


def _run(cmd, cwd, check=True):
    """Run a shell command in the given directory."""
    return subprocess.run(
        cmd, shell=True, cwd=cwd, capture_output=True, text=True, check=check
    )


def _init_drifter_repo(root):
    """Create a minimal drifter project in a temp directory with git initialized."""
    _run("git init", root)
    _run("git config user.email 'test@test'", root)
    _run("git config user.name 'test'", root)

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

    # Commit the initial state
    _run("git add .", root)
    _run("git commit -m 'initial'", root)


def _gate(root, branch=None):
    """Run `drifter gate` in the given directory. Returns CompletedProcess."""
    cmd = [DRIFTER_BIN, "gate"]
    if branch:
        cmd.extend(["--branch", branch])
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Test: py_compile catches syntax errors
# ---------------------------------------------------------------------------

class TestPyCompile:
    """Verify that py_compile catches Python syntax errors."""

    def test_valid_python_passes(self, tmp_path):
        """A syntactically valid .py file should pass the gate."""
        _init_drifter_repo(tmp_path)

        # Add a valid Python file
        (tmp_path / "valid.py").write_text("x = 1 + 2\n")
        _run("git add valid.py", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 0, f"Gate should pass. stdout: {result.stdout} stderr: {result.stderr}"

    def test_syntax_error_fails(self, tmp_path):
        """A Python file with a syntax error should fail the gate."""
        _init_drifter_repo(tmp_path)

        # Add a Python file with a syntax error
        (tmp_path / "bad.py").write_text("def foo(\n")
        _run("git add bad.py", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 1, f"Gate should fail on syntax error. stdout: {result.stdout} stderr: {result.stderr}"
        assert "py_compile" in result.stdout or "FAIL" in result.stdout

    def test_syntax_error_in_harness_fails(self, tmp_path):
        """A syntax error in harness/ should also fail py_compile."""
        _init_drifter_repo(tmp_path)

        harness = tmp_path / "harness"
        harness.mkdir()
        (harness / "__init__.py").write_text("")
        (harness / "broken.py").write_text("if True:\n  print('missing colon'\n")
        _run("git add harness/", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 1
        assert "py_compile" in result.stdout or "FAIL" in result.stdout


# ---------------------------------------------------------------------------
# Test: pytest failures block commits
# ---------------------------------------------------------------------------

class TestPytest:
    """Verify that failing pytest blocks the gate."""

    def test_passing_tests_pass(self, tmp_path):
        """A passing test file should not block the gate."""
        _init_drifter_repo(tmp_path)

        # Add a test file that passes
        (tmp_path / "tests" / "test_ok.py").write_text(
            "def test_always_passes():\n    assert True\n"
        )
        _run("git add tests/", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 0, f"Gate should pass. stdout: {result.stdout} stderr: {result.stderr}"

    def test_failing_tests_block(self, tmp_path):
        """A failing test should block the gate."""
        _init_drifter_repo(tmp_path)

        # Add a test file that fails
        (tmp_path / "tests" / "test_fail.py").write_text(
            "def test_always_fails():\n    assert False, 'intentional failure'\n"
        )
        _run("git add tests/", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 1, f"Gate should fail on test failure. stdout: {result.stdout} stderr: {result.stderr}"
        assert "FAIL" in result.stdout

    def test_no_tests_skips_pytest(self, tmp_path):
        """When tests/ has no .py files, pytest should be skipped."""
        _init_drifter_repo(tmp_path)

        # Add a non-test file
        (tmp_path / "tests" / "README.txt").write_text("no tests here\n")
        _run("git add tests/", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Test: migration safety rejects modified migrations
# ---------------------------------------------------------------------------

class TestMigrationSafety:
    """Verify that modifying an existing migration is rejected."""

    def test_new_migration_passes(self, tmp_path):
        """Adding a new migration file should pass."""
        _init_drifter_repo(tmp_path)

        # Add a new migration
        (tmp_path / "rust" / "migrations" / "20240102000000_add_users.sql").write_text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY);\n"
        )
        _run("git add rust/migrations/", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 0, f"Gate should pass. stdout: {result.stdout} stderr: {result.stderr}"

    def test_modified_migration_fails(self, tmp_path):
        """Modifying an existing migration should fail the gate."""
        _init_drifter_repo(tmp_path)

        # Modify the existing migration
        migration = tmp_path / "rust" / "migrations" / "20240101000000_init.sql"
        migration.write_text(
            "CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, name TEXT);\n"
        )
        _run("git add rust/migrations/", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 1, f"Gate should fail on modified migration. stdout: {result.stdout} stderr: {result.stderr}"
        assert "modified" in result.stdout.lower() or "FAIL" in result.stdout


# ---------------------------------------------------------------------------
# Test: cargo check catches Rust errors
# ---------------------------------------------------------------------------

class TestCargoCheck:
    """Verify that cargo check catches Rust compilation errors."""

    def test_valid_rust_passes(self, tmp_path):
        """Valid Rust code should pass cargo check."""
        _init_drifter_repo(tmp_path)

        # Modify main.rs with valid Rust
        (tmp_path / "rust" / "src" / "main.rs").write_text(
            'fn main() { let x = 42; println!("{}", x); }\n'
        )
        _run("git add rust/", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 0, f"Gate should pass. stdout: {result.stdout} stderr: {result.stderr}"

    def test_invalid_rust_fails(self, tmp_path):
        """Invalid Rust code should fail cargo check."""
        if not shutil.which("cargo"):
            pytest.skip("cargo not on PATH")
        _init_drifter_repo(tmp_path)

        # Modify main.rs with invalid Rust
        (tmp_path / "rust" / "src" / "main.rs").write_text(
            'fn main() { let x: String = 42; }\n'  # type mismatch
        )
        _run("git add rust/", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 1, f"Gate should fail on invalid Rust. stdout: {result.stdout} stderr: {result.stderr}"
        assert "FAIL" in result.stdout

    def test_missing_function_fails(self, tmp_path):
        """Rust code calling a non-existent function should fail."""
        if not shutil.which("cargo"):
            pytest.skip("cargo not on PATH")
        _init_drifter_repo(tmp_path)

        (tmp_path / "rust" / "src" / "main.rs").write_text(
            'fn main() { does_not_exist(); }\n'
        )
        _run("git add rust/", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 1


# ---------------------------------------------------------------------------
# Test: immutable file protection
# ---------------------------------------------------------------------------

class TestImmutableFiles:
    """Verify that immutable files cannot be changed."""

    def test_constitution_change_fails(self, tmp_path):
        """Modifying constitution.md should fail."""
        _init_drifter_repo(tmp_path)

        (tmp_path / "constitution.md").write_text("# New Constitution\n")
        _run("git add constitution.md", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 1
        assert "immutable" in result.stdout.lower() or "FAIL" in result.stdout

    def test_drifter_toml_change_fails(self, tmp_path):
        """Modifying drifter.toml should fail."""
        _init_drifter_repo(tmp_path)

        (tmp_path / "drifter.toml").write_text('api_key = "changed"\n')
        _run("git add drifter.toml", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 1
        assert "immutable" in result.stdout.lower() or "FAIL" in result.stdout


# ---------------------------------------------------------------------------
# Test: no changes passes
# ---------------------------------------------------------------------------

class TestNoChanges:
    """Verify that a clean repo passes the gate."""

    def test_no_changes_passes(self, tmp_path):
        """A repo with no uncommitted changes should pass."""
        _init_drifter_repo(tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 0
        assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test: agent sovereignty — agents cannot modify other agents' files
# ---------------------------------------------------------------------------

class TestAgentSovereignty:
    """Verify that agent branches cannot modify other agents' directories."""

    def test_agent_own_files_pass(self, tmp_path):
        """An agent branch modifying its own files should pass."""
        _init_drifter_repo(tmp_path)

        agent_dir = tmp_path / "agents" / "engineer"
        agent_dir.mkdir(parents=True)
        (agent_dir / "session.md").write_text("# Session\n")
        _run("git add agents/", tmp_path)

        result = _gate(tmp_path, branch="agent/engineer/fix-session")
        assert result.returncode == 0, f"stdout: {result.stdout}"

    def test_agent_other_files_fail(self, tmp_path):
        """An agent branch modifying another agent's files should fail."""
        _init_drifter_repo(tmp_path)

        other_dir = tmp_path / "agents" / "sales-strategist"
        other_dir.mkdir(parents=True)
        (other_dir / "AGENTS.md").write_text("# Sales\n")
        _run("git add agents/", tmp_path)

        result = _gate(tmp_path, branch="agent/engineer/create-agents")
        assert result.returncode == 1, f"stdout: {result.stdout}"
        assert "agents cannot modify other agents' files" in result.stdout

    def test_agent_shared_code_pass(self, tmp_path):
        """An agent branch modifying shared code (not agents/) should pass."""
        _init_drifter_repo(tmp_path)

        harness_dir = tmp_path / "harness"
        harness_dir.mkdir(parents=True)
        (harness_dir / "__init__.py").write_text("")
        (harness_dir / "utils.py").write_text("x = 1\n")
        _run("git add harness/", tmp_path)

        result = _gate(tmp_path, branch="agent/engineer/add-utils")
        assert result.returncode == 0, f"stdout: {result.stdout}"

    def test_non_agent_branch_can_modify_any(self, tmp_path):
        """A non-agent branch (human/Daniel) can modify any agent's files."""
        _init_drifter_repo(tmp_path)

        agent_dir = tmp_path / "agents" / "engineer"
        agent_dir.mkdir(parents=True)
        (agent_dir / "session.md").write_text("# Session\n")
        _run("git add agents/", tmp_path)

        # No --branch flag, or a non-agent branch
        result = _gate(tmp_path, branch="main")
        assert result.returncode == 0, f"stdout: {result.stdout}"

    def test_agent_migration_rejected_with_branch_flag(self, tmp_path):
        """Migration restriction works via --branch flag (detached worktree fix)."""
        _init_drifter_repo(tmp_path)

        (tmp_path / "rust" / "migrations" / "20240201000000_new.sql").write_text(
            "CREATE TABLE new (id INTEGER PRIMARY KEY);\n"
        )
        _run("git add rust/migrations/", tmp_path)

        result = _gate(tmp_path, branch="agent/engineer/add-migration")
        assert result.returncode == 1, f"stdout: {result.stdout}"
        assert "agents cannot create database migrations" in result.stdout


# ---------------------------------------------------------------------------
# Test: path antipattern — gateways must not use Path(__file__) for project root
# ---------------------------------------------------------------------------

class TestPathAntipattern:
    """Verify that gateway files cannot use Path(__file__) for project root."""

    def test_gateway_with_antipattern_fails(self, tmp_path):
        """A gateway file deriving project_root from __file__ should fail."""
        _init_drifter_repo(tmp_path)

        gateways = tmp_path / "gateways"
        gateways.mkdir()
        (gateways / "example.py").write_text(
            'from pathlib import Path\n'
            'project_root = Path(__file__).resolve().parent.parent\n'
        )
        _run("git add gateways/", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 1, f"stdout: {result.stdout}"
        assert "derives project_root from __file__" in result.stdout

    def test_gateway_without_antipattern_passes(self, tmp_path):
        """A gateway file accepting project_root as parameter should pass."""
        _init_drifter_repo(tmp_path)

        gateways = tmp_path / "gateways"
        gateways.mkdir()
        (gateways / "example.py").write_text(
            'from pathlib import Path\n'
            'def main(project_root: Path) -> int:\n'
            '    return 0\n'
        )
        _run("git add gateways/", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 0, f"stdout: {result.stdout}"

    def test_dashboard_with_antipattern_fails(self, tmp_path):
        """The check also applies to dashboard/ files."""
        _init_drifter_repo(tmp_path)

        dashboard = tmp_path / "dashboard"
        dashboard.mkdir()
        (dashboard / "app.py").write_text(
            'from pathlib import Path\n'
            'ROOT = Path(__file__).resolve().parent.parent\n'
        )
        _run("git add dashboard/", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 1, f"stdout: {result.stdout}"

    def test_non_gateway_file_with_pattern_passes(self, tmp_path):
        """__file__ usage in non-gateway files should not be flagged."""
        _init_drifter_repo(tmp_path)

        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "helper.py").write_text(
            'from pathlib import Path\n'
            'ROOT = Path(__file__).resolve().parent.parent\n'
        )
        _run("git add scripts/", tmp_path)

        result = _gate(tmp_path)
        assert result.returncode == 0, f"stdout: {result.stdout}"
