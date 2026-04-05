# Session: Scheduler system notification filtering

**Date:** 2026-04-05

## What happened

The scheduler was spawning full OpenCode worker cycles every time system notifications (auto-merge PASS, auto-deploy OK) landed in the engineer's inbox. These notifications come from `from_agent: "system"` and require no action, but the scheduler only checked `bool(items)` and spawned a worker for any non-empty inbox. The engineer would read the notifications, say "no actionable tasks", ack them, and exit -- wasting API calls and ~60s per cycle.

## Fix applied

### `harness/scheduler.py`

- Replaced `_has_inbox` (returns bool) with `_get_inbox` (returns parsed item list)
- Added `_ack_inbox` helper that batches IDs into a single `drifter ack` call
- Partitioned inbox processing in `main()`:
  - If any item has `from_agent != "system"` -> spawn worker normally (handles everything)
  - If ALL items are from "system" -> ack directly, skip worker
- After acking system-only items, falls through to dream check instead of returning (prevents system notifications arriving every 2 min from permanently starving dream cycles)

## Key decisions

- Filter by `from_agent == "system"` only, not by trigger type or content. Simple, safe -- if a field is missing, `get()` returns None != "system", so the item is treated as actionable (safe default).
- Batch acking uses `drifter ack id1 id2 id3` (Rust CLI accepts `Vec<i64>`), one subprocess instead of N.
- No Rust CLI changes, no new config, no worker changes.

## Verification

- `from_agent` field confirmed against: Rust `InboxItem` struct (`bus.rs:52`), SQL alias (`bus.rs:336`), Python consumer (`memory.py:107`), and live VPS JSON output
- `py_compile` clean, 41 tests pass

## State at session end

- `harness/scheduler.py` modified, not yet committed
- VPS still running previous version (will update on next deploy after commit+push)
