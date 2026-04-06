# Session: tensions scheduler trigger

**Date:** 2026-04-06

## What happened

Diagnosed why the engineer agent was idle for 17+ cycles despite having non-empty tensions from dream cycles. The dream → tension → action loop was architecturally broken: the scheduler checked inbox, rejected branches, and dream deadline, but never checked tensions.md. The `has_work` calculation in memory.py included tensions, but the scheduler (the gatekeeper) never spawned a worker to reach that code.

Fixed by adding tensions as Priority 2.5 in the scheduler (between rejected branches and dream) with a 4-hour cooldown matching the dream interval. Added `_has_tensions()` and `_tensions_cooldown_elapsed()` to scheduler.py. Added tension-specific prompt instructions in memory.py: fix what you can directly, escalate what needs Daniel via #engineering post. Worker records `last_tensions_cycle_at` in state on success. Unified delta suppression for both rejected and tensions triggers.

Also identified 7 stale merged remote branches under `agent/engineer/` (build-gateways, fix-gate-test, scheduler-tests, session-handoff-2, session-handoff-3, transcripts-gateway-tests, update-session) as future cleanup — not addressed this session.

## Key insight

The dream → tension → action loop was designed but never completed. Dreams write tensions, the prompt compiler reads tensions, but the scheduler (the gatekeeper between them) didn't check tensions. If the scheduler doesn't trigger on an artifact, downstream prompt logic that reads it is dead code. This is a specific instance of the system enforcement principle: anything that matters must be enforced by the system, not by the model.

## Commits

- `fba4d08` — Close dream→tension→action loop: scheduler triggers on non-empty tensions

## State at session end

- All changes deployed to VPS
- Engineer agent woke up at 01:40Z with "tensions need attention" — first tensions-triggered cycle
- OpenCode/Nemotron running, working on tensions prompt
- 7 stale merged branches still on origin (future cleanup)
