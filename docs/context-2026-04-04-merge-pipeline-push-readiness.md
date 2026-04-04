# Session: Merge Pipeline Comparison + Push Readiness Audit

**Date:** 2026-04-04
**Branches compared:** build/cc, build/codex (auto-merge/deploy pipeline)

## What happened

Compared two competing implementations of PRD section 9 (auto-merge + auto-deploy) against the spec. Selected `build/codex` as the winner for its two-worktree gate integration (squash-merge produces uncommitted changes matching `drifter gate`'s `git diff --name-only HEAD` interface) and CAS `update-ref` for race-proof main advancement.

Cherry-picked from `build/cc`: deploy notifications (post to #engineering on success, `drifter notify` on rollback), systemd-first worker restart with PID-file fallback, remote sync (fetch origin, push main, delete remote agent branches), and separate CONFLICT/REJECT/PASS/SKIP labels for merge conflicts vs gate failures. Replaced destructive `git reset --hard HEAD` (ran every deploy cycle) with `git checkout main` only when deploying.

Fixed OpenCode CLI invocation in `harness/worker.py`: removed non-existent `--auto` flag, switched model passing from temporary `opencode.json` backup/restore to `--model` CLI flag. This eliminated a race condition and ~25 lines of config gymnastics.

Switched all models to `openrouter/qwen/qwen3.6-plus:free` in both `drifter.toml` and `agents/engineer/agent.toml`.

Ran the engineer agent successfully. It autonomously: read TASKS.md, found the gate testing task, explored `gate.rs` and `paths.rs`, wrote 14 integration tests in `tests/test_gate.py`, fixed a project-root detection issue in the test fixtures, ran all tests to green, committed, and posted to #engineering. This validates the full loop: worker -> prompt compiler -> OpenCode -> code -> commit -> bus post.

## Push readiness audit

- **Bootstrap gap fixed:** Added `ensure_agent_registered()` to worker.py. On startup, checks `drifter agents --json` and runs `drifter birth` + `drifter watch` if the agent isn't in the DB. Fresh clones now bootstrap automatically.
- **Secrets:** Gitignored `drifter.toml` (contains API key). Created `drifter.toml.example` as the tracked template with `YOUR_KEY_HERE`.
- **.gitignore gaps fixed:** Added `agents/*/heartbeat.md`, `agents/*/tensions.md`, `*.lock` (with `!rust/Cargo.lock` exception), `drifter-prompt-*.md`, `.drifter/` state directory.
- **Config:** `agent.toml` restored with full sections (`[channels]`, `[limits]`, `[worker]`), model set to confirmed-working `openrouter/qwen/qwen3.6-plus:free`.
- **README:** Updated from build-phase checklist to actual quick start: `cargo build`, `drifter init`, `python3 -m harness.worker --agent engineer`.

## Key decisions

1. **Codex over cc** because the two-worktree approach correctly interfaces with the gate, and CAS update-ref prevents merge races. cc's simpler single-worktree approach creates committed changes that the gate wouldn't see via `git diff --name-only HEAD`.
2. **`--model` CLI flag over config file** eliminates the backup/restore dance and race conditions between concurrent workers.
3. **`drifter.toml` gitignored** because it holds API keys. Template ships as `.example`.
4. **Context docs kept in repo** as project journey documentation.

## Files changed

- `scripts/common.sh` — added `sync_main_with_origin()`, systemd-first `restart_workers()`
- `scripts/auto-merge.sh` — remote sync, origin branches, push/delete, CONFLICT/REJECT/PASS/SKIP labels
- `scripts/auto-deploy.sh` — `git checkout main` instead of `reset --hard`, deploy notifications
- `harness/worker.py` — removed `--auto`, added `--model`, added `ensure_agent_registered()`
- `.gitignore` — comprehensive runtime artifact coverage
- `README.md` — actual quick start
- `agents/engineer/agent.toml` — model update, full config sections
- `drifter.toml` → gitignored, `drifter.toml.example` added
- `tests/test_gate.py` — 14 integration tests (committed by the engineer agent)
