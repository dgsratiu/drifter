# Session: REJECT feedback loop fix + rejected-branches tracking + engineer self-awareness

**Date:** 2026-04-05

## What happened

Three systemic fixes to make the engineer agent autonomous, building on the earlier ack-on-success and migration fixes.

### 1. REJECT feedback loop

Auto-merge REJECT/CONFLICT messages were posted with `--agent system --type system`. The scheduler checked `from_agent != "system"` and auto-acked all system messages — including actionable REJECTs. The engineer never saw gate failures.

**Fix:** Added `post_engineering_error()` in `scripts/common.sh` that posts with `--type error`. Added `msg_type` field to Rust `InboxItem` struct. Scheduler now checks both `from_agent` and `msg_type` — system+error = actionable, system+system = informational.

### 2. Rejected-branches tracking

Auto-merge now records `branch sha` pairs in `.drifter/rejected-branches` after REJECT/CONFLICT. Before processing a branch, checks if already rejected at same SHA (skip — no new commits) or new SHA (clear entry, re-process). Entries cleaned up on merge success or branch deletion.

### 3. Engineer self-awareness (the root cause fix)

The engineer's work loop was entirely inbox-driven — if a notification was missed for ANY reason, work stalled forever. Fixing notification delivery (REJECT as error, ack-on-success, circuit breaker) is band-aid work.

**Root cause fix:** Make the engineer introspective about its own state.
- Scheduler reads `.drifter/rejected-branches` as a third trigger (after inbox, before dream) with 10-min cooldown
- Prompt assembly includes "Rejected Branches" section listing the agent's rejected branches
- `has_work` includes `has_rejected` so rejected branches are treated as pending work

The engineer now discovers its pending work by reading its own state, not by waiting for messages.

## Key insight

Autonomous agents should be self-aware of their own state (branches, pending work, failures) via filesystem/DB introspection. Notifications are hints; introspection is ground truth. If a notification is missed, the agent should still discover the work on its next cycle.

## Commits

- `6fa342e` — Fix REJECT feedback loop + rejected-branches tracking
- `f204351` — Make engineer self-aware of rejected branches

## State at session end

- All fixes deployed to VPS via auto-deploy
- Engineer has rejected branch `transcripts-gateway-tests` (3 failing tests from Qwen 502 crash)
- Scheduler will trigger the engineer via rejected-branches trigger on next cycle (10-min cooldown)
- Engineer should discover the branch, fix the tests, and push — no manual nudge needed
