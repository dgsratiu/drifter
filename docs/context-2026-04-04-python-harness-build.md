# Context: Python Harness Build

Date: 2026-04-04

The session built the Phase 2 Python harness in `harness/` against the actual Rust kernel implementation, not just the PRD. The implemented CLI surface in `rust/src/main.rs` was treated as authoritative, with supporting reads from `rust/src/bus.rs`, `rust/src/inbox.rs`, `rust/src/birth.rs`, and `rust/src/paths.rs`.

Files added:

- `harness/common.py`
- `harness/memory.py`
- `harness/worker.py`
- `harness/health.py`
- `harness/__init__.py`

What was built:

- Shared path/config/state helpers in `harness/common.py`
- Prompt compilation for regular and dream cycles in `harness/memory.py`
- Worker loop with wake-file handling, heartbeat handling, dream timing, inbox acking, phase-1 `opencode.json` lock/write behavior, and local runtime state persistence in `harness/worker.py`
- Health/status reporting from local worker state in `harness/health.py`

Important implementation decisions:

- The harness reads the real `drifter` commands that exist today: `inbox`, `read`, `ack`, `channels`, and `metrics`.
- The worker uses `agents/<name>/state.json` as the runtime source of truth for cursors and local cycle bookkeeping.
- `agent.toml` parsing is tolerant of both the richer nested PRD shape and the minimal birth-generated shape already present in the kernel.
- The phase-1 PRD model-selection approach was implemented by taking a filesystem lock, writing `opencode.json` at the project root, running `opencode`, and then restoring/removing that file.

Validation performed:

- `python3 -m py_compile harness/*.py`
- `python3 -c "import harness.common, harness.memory, harness.worker, harness.health; print('imports ok')"`
- `python3 -m harness.memory --agent engineer`

Observed limitation:

- The current Rust kernel does not expose a write-side worker interface for metrics or for updating `agents.last_cycle_at` / `agents.last_dream_at`. Because of that, the harness currently stores those runtime fields in `agents/<name>/state.json` instead of the database.

Environment limitation:

- `opencode` was not installed in the session environment, so a full end-to-end worker spawn test was not run.

Excluded from the save artifact:

- `agents/engineer/heartbeat.md`
- `agents/engineer/tensions.md`

