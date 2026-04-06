# Session: birth flow docs + CLAUDE.local.md

**Date:** 2026-04-06

## What happened

Three things in one session:

1. **Orphaned agent cleanup** (already committed as `a069290`): deleted `agents/meeting-analyst/` and `agents/sales-strategist/` — created by engineer before sovereignty gate existed, never born via `drifter birth`.

2. **CLAUDE.local.md**: created a gitignored local file with VPS deployment context (SSH alias `drifter-vps`, repo path `/home/drifter-agent/drifter`, quick commands). When asked to "check the engineer," future sessions will SSH to VPS instead of reading stale local state.json. Added `CLAUDE.local.md` to `.gitignore`.

3. **Birth flow documentation**: discovered the engineer agent didn't understand the birth pipeline. The CLI_REFERENCE in `harness/memory.py` listed `drifter propose` but not the full flow (propose → Daniel approves → system births). The engineer saw "meeting-analyst inactive" in tensions and created AGENTS.md files directly — a logical improvisation given incomplete information, not disobedience. Expanded CLI_REFERENCE LIFECYCLE section with 3 lines documenting the full pipeline and that direct file creation is gate-blocked.

## Key decisions

1. **CLAUDE.local.md over .claude/rules/** — CLAUDE.local.md is the built-in Claude Code mechanism for per-machine gitignored context. Better than a gitignored rules file because it's standard and auto-loaded at the right level.
2. **No sensitive credentials in CLAUDE.local.md** — only SSH aliases (resolved from ~/.ssh/config), paths, and command patterns. IP addresses kept out in case the file is accidentally committed.
3. **3 lines in CLI_REFERENCE, not a separate doc** — agents see CLI_REFERENCE every cycle. Adding the birth flow there ensures they see it without needing to read docs/.

## Commits

- `a069290` — orphaned agent cleanup (previous in this session)
- (pending) — birth flow docs + CLAUDE.local.md + context doc

## State at session end

- VPS engineer is healthy and idle (last dream 20:44 UTC, scheduler running every 2 min)
- After push + auto-deploy, the engineer will see the expanded CLI_REFERENCE on its next cycle
- Stale tensions on VPS still reference meeting-analyst/sales-strategist — next dream cycle will rewrite them
