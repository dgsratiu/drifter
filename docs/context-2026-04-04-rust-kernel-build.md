# Context: Rust Kernel Build

**Date:** 2026-04-04
**Branch:** build/cc

## What was done

Built the Rust kernel (`drifter` CLI binary) — 1,276 LOC across 7 source files in `rust/src/`. All 20 CLI subcommands implemented per PRD section 3:

- **bus.rs** (475 lines): channels, messages, agents, proposals, metrics, watchers, rate limiting, seq counter, metadata merge
- **main.rs** (427 lines): clap CLI with all 20 subcommands, DB connection, dispatch
- **gate.rs** (121 lines): quality gate — cargo check, py_compile, import check, pytest, migration immutability
- **inbox.rs** (106 lines): @mention parsing, @all broadcast, watcher routing, wake file touches
- **birth.rs** (63 lines): agent creation — dirs, soul, agent.toml, DB registration, #internal watch, announcements
- **notify.rs** (60 lines): ntfy.sh notifications (topic from env or drifter.toml)
- **policy.rs** (24 lines): file policy — immutable files, agent isolation

Also created: `rust/Cargo.toml` (deps: clap, sqlx, serde, reqwest, tokio, uuid, chrono, toml) and `rust/migrations/20240101000000_init.sql` (schema from schema.sql).

## Prior work already in place

A previous session (commit 77355bc) added a `paths` module and threaded `project_root: &Path` through all functions for proper path resolution. Also improved gate.rs to catch untracked files and Cargo.toml changes, and fixed SQLite datetime functions in rate limiting and metrics queries.

## Incident

During smoke testing, `drifter birth engineer` was run against a test DB, which overwrote `agents/engineer/agent.toml` (committed content) with minimal test data. Then `rm -rf agents/engineer` deleted the committed soul file and agent.toml. Both were restored via `git checkout HEAD -- agents/engineer/`. Additional test artifacts (.wake, memory/, session.md, state.json, heartbeat.md, tensions.md) were also cleaned up.

## What's next (PRD phases 2-4)

- Phase 2: Python harness — memory.py, worker.py, health.py (~400 LOC)
- Phase 3: Dashboard — FastAPI + htmx + SSE (~300 LOC)
- Phase 4: Auto-merge/deploy scripts (~100 LOC shell)
