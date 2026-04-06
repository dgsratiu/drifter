# Session: autoagent analysis + sovereignty gate

**Date:** 2026-04-06

## What happened

Explored [kevinrgu/autoagent](https://github.com/kevinrgu/autoagent) — a meta-agent engineering system inspired by Karpathy's autoresearch. Produced an honest analysis: autoagent is a benchmark optimizer, not an autonomous agent system. Its clever ideas are the closed feedback loop (change->measure->keep/discard), fixed adapter boundary, overfitting test ("if this task disappeared, would this still be worthwhile?"), and simplicity criterion (equal performance + less code = keep). Most architectural choices (single-file, Docker isolation, ephemeral agents, "NEVER STOP" loop) don't transfer — Drifter already does these things better with persistent identity, cron-based autonomy, and system enforcement.

Checked on the engineer agent: 5 tensions cycles ran but tensions never cleared. Root cause: the engineer was impersonating other agents (posting as `meeting-analyst`, creating files in `agents/sales-strategist/`, `agents/digest/`). The constitution says "never touch another agent's files" but Nemotron (free tier) ignores soft constraints. The birth flow already handles agent creation through the bus (propose -> approve -> system creates files), so agents never need to write to other agents' directories.

Implemented agent sovereignty enforcement in the gate: agent branches cannot modify `agents/<other>/` directories. Also discovered and fixed a pre-existing bug — auto-merge runs the gate in detached worktrees where `current_branch()` returns "HEAD", so the migration restriction was silently broken. Added `--branch` CLI flag to `drifter gate` so auto-merge can pass the actual branch name. Added architectural invariants section to the rules file documenting 9 gate-invisible invariants. Fixed 3 pre-existing broken transcript tests where the engineer wrote tests using content strings already in the real project's state file.

## Key insights

1. AutoAgent's biggest lesson for Drifter is that its feedback loop is open: the gate asks "does it compile?" but never "did it improve anything?" Closing that loop is the highest-leverage future work.
2. Agent sovereignty must be system-enforced, not model-enforced. The engineer (Nemotron) ignored the constitution's sovereignty rule entirely.
3. The gate's detached worktree bug meant the migration restriction was never firing during auto-merge — any branch-based check silently failed.
4. Gateway code that resolves `project_root` from `__file__` creates time-bomb tests that pass at gate time but break after the gateway's first real run (state file poisons test results).

## Commits

- `75daa8c` — Agent sovereignty gate + architectural invariants rules + fix transcript tests

## State at session end

- All changes pushed to origin
- Engineer agent idle on VPS, next tensions cycle ~19:50Z, next dream ~20:31Z
- Sovereignty gate will reject the engineer's next attempt to create files in other agents' dirs
- ~/autoagent cloned locally for reference (not part of drifter repo)
- Pending: gate check for hardcoded __file__ path resolution pattern (discussed but not implemented)
