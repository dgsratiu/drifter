# Session: auto-merge remote branch cleanup

**Date:** 2026-04-06

## What happened

Auto-merge (`scripts/auto-merge.sh`) deleted local agent branches after merging but never deleted the corresponding remote branches on GitHub. They accumulated forever — 7 remote `agent/*` branches existed, 5 already merged into main. The fix adds remote branch deletion in two layers: (1) inline `git push origin --delete` at both existing local-delete sites (successful merge at line 96 and stale-leftover cleanup at line 112), and (2) a safety-net pass after the main branch-processing loop that scans `refs/remotes/origin/agent/` for branches merged into main with no local counterpart.

All remote deletes are non-fatal (`|| true`) and guarded by `remote get-url origin` checks. The safety net skips branches that still have local counterparts (those are handled by the main loop) and only deletes branches confirmed merged via `merge-base --is-ancestor`.

## Key decisions

1. **Two-layer approach** — inline delete for immediate cleanup + safety-net pass for historical accumulation and failed inline deletes
2. **Non-fatal remote deletes** — merge is already done and pushed, remote branch is just clutter; network failures or race conditions are harmless
3. **Safety net uses `merge-base --is-ancestor`** — only deletes branches confirmed merged into main; active/rejected branches are untouched
4. **No new tests** — auto-merge is a bash script tested via cron on VPS, consistent with existing test strategy

## Commits

- (pending) — Auto-merge remote branch cleanup

## State at session end

- Changes committed but not pushed
- 5 stale merged remote branches will be cleaned up on next auto-merge run on VPS
- 2 unmerged remote branches (`build-gateways`, `update-session`) will be left alone correctly
