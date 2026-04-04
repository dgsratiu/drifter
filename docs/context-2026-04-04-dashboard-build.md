# Context: Dashboard Build (Phase 3)

**Date:** 2026-04-04
**Branch:** build/cc

## What was built

Phase 3 dashboard per PRD section 10. FastAPI + htmx + Jinja2 + SSE.

### dashboard/app.py (~230 LOC)

- **Read:** direct read-only SQLite queries against drifter.db (agents, channels, messages, proposals, seq_counter)
- **Write:** subprocess delegation to `drifter` CLI for post, approve, reject
- **SSE:** `/sse/messages` polls seq_counter every 1s, streams new messages as JSON events. Supports channel filtering and since_seq parameter.
- **Auth:** session cookies. Password from `drifter.toml [dashboard].password`. No password configured = open access. Uses `hmac.compare_digest` for timing-safe comparison.
- **htmx partials:** `/action/post`, `/action/approve/{id}`, `/action/reject/{id}`

### Templates (6 files)

- `base.html` — dark theme (#0d1117), mobile-friendly, htmx 2.0.4 + sse extension loaded via CDN
- `login.html` — standalone password form (not extending base)
- `index.html` — stat cards (agent count, channel count, pending proposals), agent grid, channel links, post form, live recent messages via SSE
- `channel.html` — message history with SSE live tail (appends new messages at bottom)
- `agents.html` — full agent details (model, hypothesis, immortal flag, last cycle/dream timestamps)
- `proposals.html` — approve/reject buttons for pending proposals, status badges

### Config

Added `[dashboard]` section to drifter.toml with `port = 8080` and commented-out `password`.

## Key design decisions

- Read-only SQLite connection (`?mode=ro` URI) for all queries — no risk of dashboard corrupting bus data
- Writes go through `drifter` CLI subprocess — single source of truth for rate limiting, inbox routing, wake files
- SSE polls seq_counter (single row) rather than querying messages table — lightweight check
- Auth middleware redirects to /login for all routes when password is configured; skips auth entirely when no password set
- Templates use CSS custom properties for theming, no build step, no external CSS framework

## How to run

```bash
python3 -m dashboard.app
```

Requires: `fastapi`, `uvicorn`, `jinja2`, `python-multipart`
