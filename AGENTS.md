# AGENTS.md

## What This Is

Drifter is an environment where artifacts generate knowledge automatically. `docs/prd.md` is the complete spec.

Rust kernel + Python harness + OpenCode agents. Each agent IS an OpenCode session.

## Architecture

```
Humans → Dashboard (FastAPI) → drifter CLI (Rust) → SQLite ← Workers (Python) → OpenCode
```

## Build Sequence

**Phase 1:** Build `rust/` — the kernel. All CLI commands per PRD section 3. Schema in `schema.sql`.
**Phase 2:** Build `harness/` — worker.py, memory.py, health.py. Thin Python that spawns OpenCode.
**Phase 3:** Build `dashboard/` — FastAPI + htmx + SSE.
**Phase 4:** Build `scripts/` — auto-merge + auto-deploy.
**Phase 5:** Start the engineer agent. It reads TASKS.md and builds everything else.

## Rules

- Rust for the kernel. Python for the harness. OpenCode for intelligence.
- SQLite is truth. WAL mode. sqlx compile-time queries.
- Small files. Nothing over 400 lines.
- Daniel to main. Agents to `agent/<n>/<topic>` branches.

## Files

- `docs/prd.md` — the complete spec
- `constitution.md` — laws, rights (in every agent prompt)
- `schema.sql` — database schema
- `agents/engineer/AGENTS.md` — engineer's soul
- `agents/engineer/agent.toml` — engineer's config
- `TASKS.md` — engineer's backlog
- `drifter.toml` — API key
