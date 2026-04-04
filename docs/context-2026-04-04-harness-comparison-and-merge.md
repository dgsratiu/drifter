# Context: Python Harness Comparison and Merge

**Date:** 2026-04-04
**Commit:** d0a6098

## What happened

Compared two Python harness implementations (build/cc and build/codex branches) against PRD section 5. Used parallel Explore agents reading via `git show branch:path` to inventory both branches without checkout thrash.

**build/codex** (723 LOC, 4 files) won on architecture: shared common.py with frozen dataclasses (AgentPaths, AgentConfig), PromptBundle carrying prompt text + inbox IDs + cursor updates + has_work flag, DRY code, 30% less LOC, spec-complete dream instructions (8 outputs including predictions and birth proposals).

**build/cc** (1058 LOC, 3 files) won on three runtime features: inotify-based wake file watching, cycle metrics recording to SQLite, and stuck detection with drifter notify.

Shipped build/codex as the base, then cherry-picked (re-implemented, not git cherry-pick) the three build/cc features onto main.

## Changes applied (d0a6098)

### harness/worker.py
- **inotify wake**: `_wait_for_event()` blocks on `inotify_simple` (CREATE/MODIFY on agent dir), falls back to `_wait_poll()` with 2s sleep loop. Returns `'wake'`/`'poll'`/`'dream'`. The main loop now blocks instead of checking booleans and sleeping idle.
- **`_dream_deadline()`**: Converts wall-clock `last_dream_at` from state into monotonic deadline for `_wait_for_event`. Returns `inf` if dreams disabled.
- **`update_post_metrics()` returns int**: 0 (no posts) or 1 (at least one post). Feeds both state counters and CycleMetrics.
- **`run_regular_cycle()` returns `tuple[bool, int]`**: (worked, posts_this_cycle).
- **`run_dream_cycle()` returns `int`**: posts_this_cycle.
- **CycleMetrics integration**: Created per worker, `cycle_start()`/`cycle_end(posts)`/`record_metrics(cycle_id)` called around each spawn. `is_stuck()` checked after regular cycles.
- **Inbox ACK removed**: The `if bundle.inbox_ids: run_drifter("ack", ...)` block is gone. Agents call `drifter ack` themselves inside their OpenCode sessions. The harness compiles inbox items into the prompt but doesn't ACK on the agent's behalf.

### harness/health.py
- **CycleMetrics class**: Tracks consecutive_silent, total_cycles, total_posts, last_cycle_duration. `record_metrics(cycle_id)` writes 4 rows to SQLite metrics table per cycle (cycle_duration_s, consecutive_silent, total_cycles, total_posts) with context `{"source": "harness.health"}`. Best-effort: swallows `sqlite3.Error`.
- **`notify_stuck(project_root)`**: Calls `drifter notify "Stuck: <agent>"` after 5 consecutive silent cycles. Best-effort: swallows errors.

## Key design decisions

- **CycleMetrics is separate from inspect()**: health.py has two concerns now: `inspect()` for external health queries (reads state.json), and `CycleMetrics` for in-process cycle tracking (writes to SQLite). They don't share state.
- **Monotonic + wall-clock hybrid for dreams**: `_dream_deadline()` converts ISO 8601 `last_dream_at` to monotonic for precision in `_wait_for_event`, but state persists wall-clock for restart resilience.
- **cycle_start() called before run_*_cycle()**: Duration includes prompt compilation + OpenCode spawn. If no work (regular cycle returns worked=False), `cycle_end()` is skipped — no phantom silent cycles counted.
- **inotify_simple is optional**: `try: import inotify_simple` at module level. Works without it (polling fallback). Dependency for production, not for dev/test.

## Branch comparison scorecard (for reference)

| Feature | build/cc | build/codex |
|---|---|---|
| inotify wake | Yes | No (now added) |
| SQLite metrics | Yes | No (now added) |
| Stuck notification | Yes | No (now added) |
| Dream predictions + birth proposals | No | Yes |
| Frozen dataclasses | No | Yes |
| PromptBundle metadata | No | Yes |
| Shared common.py (DRY) | No | Yes |
| Inbox ACK by worker | No | Yes (now removed) |
| Config backup/restore | No | Yes |
| Total LOC | 1058 | 723 |
