# Dashboard Comparison & Merge

**Date:** 2026-04-04
**Commit:** 9399710

## What happened

Compared two competing dashboard implementations (build/cc and build/codex) against PRD section 10. Both were complete Phase 3 deliveries (~440-520 LOC) with full spec compliance (FastAPI + htmx + Jinja2 + SSE + read-only DB + subprocess writes + basic auth).

## Decision: Ship build/cc

Three factors decided it:

1. **Phone UX.** Session cookies with login page stay authenticated 7 days. HTTP Basic (codex) pops a browser dialog every session — terrible on mobile. PRD says "Daniel reads and writes from his phone."
2. **Read-only enforcement.** cc uses SQLite URI `?mode=ro` at connection level. codex just avoids writes — no enforcement. One careless query could mutate the DB.
3. **SSE architecture.** cc polls `seq_counter` (single row, 1s) and pushes targeted HTML fragments via named events. codex polls file mtime (2s) and triggers full-section reloads. cc is lower latency and more aligned with htmx SSE extensions.

## Features ported from build/codex

- `/action/kill/{agent_name}` endpoint with immortal guard suppressing the kill button
- `/action/channel-create` endpoint with inline form on overview page
- Message type dropdown (text/plan/system/result) on all post forms
- Inbox unacked count per agent on agents page (LEFT JOIN inbox query)
- Default password warning banner when no password is configured
- Metadata `[meta]` hover icon on messages (SSE fragments, overview, channel views)
- `dashboard/requirements.txt` dependency manifest

## XSS fix

The original build/cc had unescaped `e.stderr` and `str(e)` in 3 error response f-strings (post, approve, reject). Added `html.escape()` to all 5 error paths for consistency with the new kill and channel-create endpoints.

## Lessons

- When merging competing AI-generated implementations, compare against spec first to pick winner, then cherry-pick from loser. Don't mechanical-merge.
- Always `html.escape()` subprocess stderr and exception messages in HTML responses — attacker-controllable content reflects into HTML.
