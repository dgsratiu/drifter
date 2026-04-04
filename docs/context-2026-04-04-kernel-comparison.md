# Kernel Branch Comparison: build/cc vs build/codex

**Date:** 2026-04-04
**Outcome:** Shipped build/cc, applied one pattern from build/codex

## What happened

Compared two independently-built Rust kernel implementations against PRD section 3. Both branches implemented all 20 CLI commands and core features. The differences were in implementation details.

## Why build/cc won

1. **Metadata merge semantics:** cc uses `or_insert` (fills gaps without overwriting caller values). codex uses `insert` (overwrites caller-provided hostname/timestamp/agent). The PRD says "auto-merge" — fill-gaps is the correct semantic.
2. **System message rate limit bypass:** cc skips rate limiting for `type == "system"`. codex doesn't — birth announcements would fail if the birthing agent hit its rate limit.
3. **Hostname resolution:** cc uses the `gethostname` crate (POSIX syscall, always works). codex uses `std::env::var("HOSTNAME")` which falls back to "unknown" in containers/systemd.
4. **Schema correctness:** codex was missing a FK constraint on `inbox.channel_id`. cc's schema had no defects.

## What codex did better

Transaction atomicity: codex wrapped `next_seq` + message INSERT in a single SQLite transaction, so a failed insert rolls back the sequence counter. cc had these as two independent queries against the pool.

## Fix applied

Changed `bus.rs::next_seq` to accept a `&mut Transaction` instead of `&SqlitePool`, and wrapped the seq increment + message INSERT in `bus.rs::post` inside `pool.begin()` / `tx.commit()`. ~9 lines changed, compiles clean.

## Method

Used parallel Explore agents to read every source file on both branches via `git show branch:path` without checking out either branch. Produced detailed compliance tables against every PRD subsection.
