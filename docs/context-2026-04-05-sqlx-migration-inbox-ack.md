# Session: sqlx migration mismatch fix + inbox ack-on-failure fix

**Date:** 2026-04-05

## What happened

Two systemic issues discovered and fixed.

### 1. sqlx migration mismatch (24h outage)

The engineer agent was stuck with "migration 20260404000001 was previously applied but is missing in the resolved migrations." Root cause: sqlx's `migrate!()` macro embeds migrations at compile time. During development of the working_dir feature, a binary was compiled on a branch (applying the migration to `drifter.db`), then the old release binary ran and found an unknown migration in the DB.

**Fix:** `set_ignore_missing(true)` on the sqlx Migrator (`rust/src/main.rs:165`). Also added a gate check in `rust/src/gate.rs` that rejects new migration files from agent branches using `--diff-filter=A` + untracked file check.

### 2. Inbox ack-on-failure (lost tasks)

The engineer received a transcript gateway task, started building `gateways/transcripts.py`, hit a Qwen API rate limit (502), and the session crashed. But the inbox item was already acked in a `finally` block (`worker.py:129`), so the task disappeared permanently.

**Fix:** Moved `_ack_inbox()` out of the `finally` block — only acks after successful `run_opencode_cycle()`. Added circuit breaker: after 3 consecutive failures, ack items and post alert to #engineering. Also changed `run_opencode_cycle()` to re-raise `TimeoutExpired` instead of silently returning.

## Key decisions

- `set_ignore_missing(true)` chosen over auto-rebuild-on-error, separate dev DBs, or preventing `cargo build` access. Safe for SQLite — extra columns don't break queries.
- Gate check only affects `agent/*` branches (auto-merge.sh:96 scans `refs/heads/agent/`). Humans can still add migrations.
- Circuit breaker at 3 consecutive failures prevents retry storms on persistent issues (rate limits, model outages).
- Timeout re-raise: `run_opencode_cycle()` now propagates `TimeoutExpired` so timeouts are treated as failures (no ack, retry next cycle).

## Commits

- `924c99b` — Fix sqlx migration mismatch + block agents from creating migrations
- `233d32b` — Fix ack strategy: only ack inbox on successful cycle, circuit breaker at 3

## VPS operations

- SSH'd to VPS to diagnose: checked scheduler logs, worker logs, state.json, inbox/watcher tables
- Re-posted transcript gateway task after discovering the original was lost to the ack bug
- VPS path: `/home/drifter-agent/drifter/`, cron runs as `drifter-agent` user

## State at session end

- Both fixes committed and pushed, VPS auto-deploy will pick up
- Transcript gateway task re-posted as message 179 to #engineering
- `gateways/transcripts.py` exists on VPS disk (untracked) from the crashed session
- Engineer agent healthy and idle, will pick up task on next scheduler cycle
