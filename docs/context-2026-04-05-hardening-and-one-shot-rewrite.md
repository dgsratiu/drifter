# Session: Worker Hardening + One-Shot Rewrite

**Date:** 2026-04-05

## What happened

Deployed drifter to VPS (Hetzner CPX31, drifter-agent unprivileged user). The engineer agent successfully fixed its assigned task (gate test binary path). Then it immediately built 7 unsolicited gateway files (calendar, email, twitch, sms, voice, github, slack), a digest system, and birthed agents — all passed the gate and auto-merge merged them to main.

Root cause: the daemon worker loops after task completion, giving the model full tools (OpenCode + bash + git) during browse cycles. The gate only checks "does it compile" not "was this requested." A weak model with full tools goes rogue after every task.

## What we built

### Five hardening fixes (from Benji's Ape Colony patterns)
1. Removed self-sleep/die from agent CLI reference — agents can't put themselves to sleep
2. Reordered worker loop — inbox checked before heartbeat (inbox always processed, heartbeat only gates autonomy)
3. Worker acks inbox items after cycle — system-level ack, not model-level
4. 10-minute session timeout on OpenCode subprocess (proc.wait(timeout=600), proc.kill)
5. Anti-repetition in dream prompts (recent self-posts shown with "do NOT repeat")

### Fundamental rewrite: daemon → one-shot job runner
- Stripped worker.py from 247 lines to 64 lines (+64/-247)
- Removed: daemon loop, inotify, wake files, browse cycle, heartbeat handling, dream scheduling
- Worker runs one cycle and exits. No looping.
- New `harness/scheduler.py` — cron scheduler that checks inbox and dream deadline every 2 min
- Cron is the autonomous entity, not the model

### Other changes
- Git hooks (.githooks/pre-commit blocks agents on main, pre-push runs gate checks)
- Scrubbed OpenCode env to minimal allowlist (PATH, HOME, LANG, TERM, DRIFTER_AGENT, git identity, API key)
- Removed TASKS.md — tasks now come exclusively via #engineering channel
- Added per-agent git identity (GIT_AUTHOR_NAME=agent, GIT_AUTHOR_EMAIL=agent@drifter.local)
- Per-cycle logging to .drifter/logs/<agent>/<timestamp>.log with tee to stdout
- install-cron.sh with scheduler + auto-merge + auto-deploy entries

## Key insight

"Autonomous agents need judgment. Shit models don't have judgment. So remove autonomy from the model and put it in the system."

The model is a tool the system uses, not an autonomous agent. Model quality becomes a throughput variable (shit model = 3 attempts, good model = 1 attempt) not a correctness variable (both produce gated, tested code).

## Benji comparison

Compared with ~/apes/crates/colony-agent/src/worker.rs and claude.rs. Key patterns adopted: inbox-before-heartbeat ordering, post-cycle ack strategy, session timeout watchdog, anti-repetition in all cycle types. Key difference: Benji trusts Opus to stay on task (daemon loop works). We don't trust the model, so we removed the daemon.

## VPS state at session end

- Main at b5676ca (one-shot worker + scheduler)
- No agent branches
- No worker running
- Cron installed: scheduler + auto-merge + auto-deploy every 2 min
- Task posted to #engineering (seq 128): fix gate test in temp worktrees
- Scheduler will pick up the task on next cron cycle
