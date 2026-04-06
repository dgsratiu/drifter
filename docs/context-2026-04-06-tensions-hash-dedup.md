# Session: tensions hash dedup

**Date:** 2026-04-06

## What happened

Added SHA256 content-hash deduplication to the tensions scheduler trigger. The engineer agent was wasting full OpenCode sessions (30-min timeout, real API cost) re-attempting identical unresolvable tensions every 4 hours. Root cause: the scheduler only checked `_has_tensions()` (non-empty file) + `_tensions_cooldown_elapsed()` (4h since last), but never whether the content actually changed. The dream cycle rewrites `tensions.md` with the same items because nothing structurally changed, so the scheduler re-triggered indefinitely.

Fix: store SHA256 hash of `tensions.md` content in `state.json` (`last_tensions_hash`) after a successful tensions cycle. Scheduler compares current hash to stored hash — identical content is skipped with a log message, falling through to the dream check. Added `_tensions_hash()` and `_tensions_changed()` to `scheduler.py`, hash recording to `worker.py`, and 6 new tests (4 unit, 2 integration). All 110 tests pass.

## Key decisions

1. **System-enforced** (scheduler) over model-enforced (dream prompt tweaks) — follows the project's core design principle
2. **SHA256 content hash** — cheap, deterministic, correct semantics for dedup
3. **Hash recorded on SUCCESS path only** — follows the architectural invariant
4. **Falls through to dream check** when tensions are skipped (stale tensions → maybe a dream is due)
5. **Backward compatible** — no stored hash = first run = trigger (existing state.json files work unchanged)

## Commits

- (pending) — Tensions hash dedup + 6 tests + 1 rules update

## State at session end

- Changes not yet committed or pushed
- Existing tensions on VPS will trigger one more cycle (no stored hash yet), then dedup kicks in
- If the dream writes genuinely new tensions, the hash changes and the engineer is triggered correctly
