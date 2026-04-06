# Session: orphaned agent directory cleanup

**Date:** 2026-04-06

## What happened

The engineer agent, before the sovereignty gate existed (commit `75daa8c`, 2026-04-06 20:03 UTC), created AGENTS.md files in `agents/meeting-analyst/` and `agents/sales-strategist/` (commit `ef30e7a`, 18 hours earlier). These agents were never actually born via `drifter birth` — no DB registration, no agent.toml, no state files, no worktrees. The directories created a misleading state implying agents exist when they don't.

Cleanup: deleted both orphaned directories and removed 4 stale lines from the engineer's session.md referencing them. No `agents/digest/` ever existed on main despite the prior context doc mentioning the engineer tried to create one.

## Key decisions

1. **Delete, don't keep** — the constitution says "Birth proposals require Daniel's approval." These were unauthorized. If Daniel wants these agents later, proper `drifter birth` creates them correctly.
2. **Clean engineer's session.md** — removed stale "waiting on meeting-analyst/sales-strategist" references to prevent the engineer wasting tokens investigating deleted directories on its next cycle.
3. **Added sovereignty gate to architectural invariants** — the other three gate-enforced checks were listed in `drifter-project.md` but the sovereignty check was missing.

## Commits

- (pending) — Orphaned agent cleanup + context doc + 1 lesson

## State at session end

- Changes staged but not committed
- Only `agents/engineer/` remains under `agents/`
- PRD still lists meeting-analyst and sales-strategist as planned agents (correct — planned, not created)
