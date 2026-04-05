# Session: deploy separation + timeout continuity

**Date:** 2026-04-06

## What happened

Three fixes to the drifter harness, continuing from the self-awareness session.

### 1. Model switch: Qwen → Nemotron

Qwen (`qwen3.6-plus:free`) was returning 502 rate limit errors from Alibaba. Switched to `nvidia/nemotron-3-super-120b-a12b:free` in `agents/engineer/agent.toml`. Nemotron worked immediately — no more 502s.

### 2. Auto-deploy: stop workers, don't restart

Auto-deploy called `restart_workers` which spawned a one-shot worker cycle bypassing the scheduler entirely. This updated `last_cycle_at` and pushed the rejected-branches cooldown forward by ~10 minutes, delaying the self-awareness trigger we'd just deployed.

**Root cause:** `restart_workers` = `stop_workers` + `start_workers`. The `start_workers` half is unnecessary — workers are one-shot, the scheduler handles lifecycle on its 2-minute cron tick.

**Fix:** Changed `restart_workers` to `stop_workers` in both `deploy_candidate()` and `rollback()` in `scripts/auto-deploy.sh`. Auto-deploy kills stale workers; the scheduler starts fresh ones with proper priority/cooldown logic.

### 3. Session timeout + timeout handoff

The engineer was timing out at 10 minutes on legitimate work (running tests, analyzing failures, reading code) with zero continuity between cycles. Each cycle repeated the same analysis from scratch.

**Fix (two parts):**
- Increased `SESSION_TIMEOUT` from 600 (10 min) to 1800 (30 min) — enough for most test-fix cycles
- Added `_write_timeout_handoff()` in `harness/worker.py` — on timeout, reads last 40 lines of the cycle log and writes a synthetic handoff to `session.md`. The next cycle's prompt includes this via the existing "Session Handoff" section.

Only fires on `TimeoutExpired`, not `CalledProcessError` — API errors aren't useful work context.

## Key insights

1. **Deploy and scheduling are separate concerns.** Auto-deploy should never start worker cycles — it bypasses priority logic and pollutes state the scheduler depends on.
2. **Timeout without continuity is Sisyphean.** The log file already contains everything the LLM did. Making it visible to the next cycle's prompt is cheap and gives continuity.

## Commits

- `2b6bdce` — Switch engineer model to Nemotron
- `6e1293b` — Auto-deploy: stop workers only, don't restart
- `d51f1de` — Increase session timeout to 30min + write timeout handoff

## State at session end

- All fixes deployed to VPS via auto-deploy
- Engineer running on Nemotron with 30-minute timeout
- Two rejected branches: `transcripts-gateway-tests` (original) and `fix-gate-test-2` (new)
- Engineer working on fixing CargoCheck tests (cargo not on PATH in test env)
