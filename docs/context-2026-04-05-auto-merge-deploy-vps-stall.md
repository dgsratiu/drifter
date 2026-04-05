# Session: Auto-merge/deploy VPS stall diagnosis and fix

**Date:** 2026-04-05

## What happened

The engineer agent completed its fix-gate-test task and auto-merge merged it to main, but then the scheduler started failing with "No module named harness.scheduler" every 2 minutes. The VPS was stuck.

## Root cause chain

1. Engineer left working tree on `agent/engineer/fix-gate-test` after its cycle
2. Auto-merge advanced `refs/heads/main` via `update-ref` but never touched the working tree
3. Auto-deploy checked `git rev-parse --abbrev-ref HEAD` — saw the agent branch, not main — exited with "requires checkout branch to be main"
4. The `git checkout main` line that syncs the working tree was AFTER the guard, so it never ran
5. Scheduler cron ran from stale working tree which didn't have `harness/scheduler.py`

Second bug: auto-merge didn't skip already-merged branches. When `branch -D` failed (branch was checked out), the branch remained and got re-processed every 2 min, spamming duplicate "auto-merge PASS" messages to #engineering.

## Fixes applied

### `scripts/auto-deploy.sh`
- Force `git checkout -f main` before any checks (was after the branch guard)
- Removed the redundant `HEAD == main` guard — the force checkout handles it

### `scripts/auto-merge.sh`
- Added `git merge-base --is-ancestor` check to skip already-merged branches and clean them up
- Kept VPS engineer fix: `checkout -B` (force-create drifter-merge branch)

### VPS manual cleanup
- `git checkout main` to switch from stale agent branch
- `git branch -D agent/engineer/fix-gate-test` to remove stale branch

## Key insight

`git update-ref` advances a ref but does NOT touch the index or working tree. Any multi-script pipeline using `update-ref` (like auto-merge advancing main) must have a downstream step that syncs the working tree before other scripts read from it. Auto-deploy was the right place for this sync, but the guard check ran first.

## State at session end

- Main at `86eb8df` (fix auto-deploy/merge)
- VPS recovered, scheduler running, engineer active on new cycle
- No stale agent branches
- Cron pipeline: scheduler -> worker -> agent branch -> auto-merge -> auto-deploy (all working)
