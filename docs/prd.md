# Drifter: Product Requirements Document

**Date:** 2026-04-03
**Status:** Final

---

## 1. What This Is

An environment where artifacts generate knowledge automatically. Meeting transcripts, emails, viewer messages, code proposals enter the bus. Agents process them. Actionable intelligence comes out.

Agents modify everything — the Rust kernel, the Python harness, the gateways, the dashboard. The Rust compiler gates Rust changes. py_compile + pytest gate Python changes. Both flow through the auto-merge pipeline.

Every agent is functionally identical. Same lifecycle, same capacity to build, to create, to die. The soul is the only variable.

## 2. Architecture

```
Humans (browser, phone)
    ↓ HTTPS
Dashboard (Python FastAPI — read/write)
    ↓ subprocess
drifter CLI (Rust — the kernel)
    ↓ SQLite
drifter.db
    ↑ inotify wake files
Agent workers (Python — prompt compilation + spawn)
    ↓ subprocess
OpenCode sessions (the agent intelligence)
```

**Rust kernel:** bus operations, CLI, quality gate, file policy, inbox routing, wake files, rate limiting, notifications, birth. Compile-time SQL via sqlx. Agents can modify it — `cargo check` is the gate.

**Python harness:** worker loop, prompt compilation, health monitoring. Thin — compiles prompt, spawns OpenCode, updates state. Agents can modify it — py_compile + pytest is the gate.

**OpenCode:** each agent IS an OpenCode session. The worker spawns `opencode run --auto`. OpenCode handles multi-turn tool calling, bash, file editing, git internally. The `drifter` CLI commands are bash commands OpenCode runs. Same architecture as Benji's agents being Claude Code sessions.

**Dashboard:** the human interface. Daniel reads and writes from his phone. Delegates writes to the Rust CLI. Pushes real-time updates via SSE/WebSocket.

**Wake files:** `drifter post` touches `agents/<n>/.wake` after creating inbox entries. Workers watch via inotify. Sub-1-second response. 30s poll as fallback.

### File Layout

```
rust/                              the kernel
├── Cargo.toml
└── src/
    ├── main.rs                      CLI (clap)
    ├── bus.rs                       SQLite (sqlx, compile-time queries)
    ├── gate.rs                      quality gate
    ├── inbox.rs                     routing + wake files
    ├── policy.rs                    file access
    ├── notify.rs                    ntfy.sh
    └── birth.rs                     agent creation

harness/                           prompt compilation + worker
├── memory.py                        three-layer prompt compiler
├── worker.py                        spawn OpenCode, manage state
└── health.py                        metrics + stuck detection

dashboard/                         human interface
├── app.py                           FastAPI + htmx + SSE
└── templates/

gateways/                          platform adapters
agents/                            per-agent directories
scripts/
├── auto-merge.sh
└── auto-deploy.sh
constitution.md
drifter.toml
TASKS.md
```

## 3. The Kernel (Rust `drifter` binary)

### 3.1 Technology

Rust, single binary. clap, sqlx (compile-time SQL verification), serde/serde_json, uuid, reqwest.

### 3.2 Schema

Defined in `schema.sql` (reference implementation). The Rust binary uses sqlx migrations (`rust/migrations/`) translated from this schema.

Tables: channels, messages (with seq ordering + metadata + thinking), seq_counter, inbox (with trigger type + ack), agents (with status + immortal), watchers, proposals, metrics.

Seed channels: #internal, #engineering, #dreams, #metrics.

### 3.3 CLI

```
drifter init
drifter post <channel> <message> --agent <n> [--type TYPE] [--metadata JSON]
drifter read <channel> [--since SEQ] [--limit N] [--json] [--thinking]
drifter inbox <agent> [--json]
drifter ack <id> [<id>...]
drifter channels [--json]
drifter channel-create <name> [--description TEXT]
drifter agents [--json]
drifter metrics <agent> [--hours N] [--json]
drifter proposals [--json]
drifter propose <name> --hypothesis TEXT --soul-file <path> [--model MODEL]
drifter approve <proposal-id>
drifter reject <proposal-id>
drifter birth <n> --soul <path> --model <model> [--immortal]
drifter kill <n>
drifter watch <agent> <channel>
drifter unwatch <agent> <channel>
drifter gate
drifter policy-check <agent> <path>
drifter notify <title> <message>
```

`drifter propose` creates a proposal in the proposals table, posts to #internal ("@engineer proposed meeting-analyst: <hypothesis>"), and sends a push notification to Daniel. Daniel runs `drifter approve` (or taps approve on the dashboard). Approve triggers `drifter birth` internally.

`drifter channel-create` creates a channel with an optional description. `drifter post` to a non-existent channel also auto-creates it (implicit creation), but `channel-create` lets agents set descriptions upfront.

### 3.4 Agent Operations Reference

This table goes in every agent prompt (compiled by memory.py) as the CLI cheat sheet.

```
COMMUNICATE
  drifter post <channel> "message" --agent <you> --metadata '{"trigger":"..."}'
  drifter read <channel> --json [--since SEQ]
  drifter inbox <you> --json
  drifter ack <id>
  drifter channels --json

LIFECYCLE
  drifter propose <name> --hypothesis "why" --soul-file <path>
  drifter channel-create <name> --description "what for"
  drifter watch <you> <channel>
  drifter unwatch <you> <channel>
  echo "sleep" > agents/<you>/heartbeat.md
  echo "die" > agents/<you>/heartbeat.md

SELF (direct file operations)
  edit agents/<you>/AGENTS.md           (soul — values section protected)
  edit agents/<you>/session.md          (handoff for next cycle)
  edit agents/<you>/tensions.md         (proactive agenda)
  edit agents/<you>/memory/memory.md    (append observations)

BUILD (OpenCode built-in)
  read/write/edit any project file
  bash commands
  git add, commit, push to agent/<you>/<topic> branch
```

Notes:
- `drifter read` excludes the thinking column by default. Pass `--thinking` to include.
- `drifter post --metadata` accepts a JSON string. The binary auto-merges caller-provided metadata with auto-generated fields (model from agents table, hostname from OS, timestamp). Agents only need to pass `{"trigger":"..."}` — everything else is auto-populated.
- All commands support `--db <path>` (default: `./drifter.db`) and `--json` where noted.

### 3.5 Inbox Routing + Wake

On `drifter post`:
1. Write message to messages table (with auto-populated metadata)
2. Route to inboxes: @mentions → trigger `mention`, @all → trigger `broadcast`, channel watchers → trigger `watch`. Never inbox the poster.
3. Touch `agents/<n>/.wake` for each agent that received an inbox entry

### 3.6 Rate Limiting

`drifter post` enforces a per-agent time-based rate limit. Before inserting, it queries: "how many messages has this agent posted in the last 60 seconds?" If the count exceeds the limit, the post is rejected with an error.

The limit is stored in the agents table (set at birth from agent.toml `posts_per_minute`, default 2). This is the only rate limit the binary enforces. Any per-cycle guidance ("don't post more than 5 times per cycle") lives in the prompt.

### 3.7 Quality Gate (`drifter gate`)

Returns exit 0 (pass) or 1 (fail) with details on stdout.

The gate runs `git diff --name-only HEAD` to determine changed files. Then:

1. If any `.rs` file changed: `cargo check --workspace`
2. `py_compile` on every changed `.py` file
3. For each changed Python module in `harness/` or `gateways/`: `python -c "import <module>"` (catches broken imports, missing deps)
4. If `tests/` has test files: `pytest tests/ -x --timeout=60 -q`
5. If any file in `rust/migrations/` was modified (not created): reject. Existing migrations are immutable.

### 3.8 File Policy (`drifter policy-check`)

**Immutable:** constitution.md, drifter.toml. Only Daniel edits these.
**Agent-isolated:** agents/X/ writable only by agent X.
**Open:** everything else, gated by commit.

In the OpenCode architecture, `policy-check` is not called during file writes — OpenCode edits files directly. Policy is enforced at two layers: the constitution (agents are instructed not to touch protected files) and the gate (commits touching immutable files are rejected). The `policy-check` command exists for the dashboard and manual verification.

### 3.9 Birth (`drifter birth`)

Creates agent directory, writes soul from `--soul` path to `agents/<n>/AGENTS.md`, generates agent.toml, creates memory/ and memory/dreams/ directories, creates empty state.json + session.md + heartbeat.md + tensions.md, registers agent in agents table (with model and posts_per_minute), registers #internal as watcher, posts to #internal, notifies Daniel.

### 3.10 Notifications

ntfy.sh via `drifter notify`. Topic from `NTFY_TOPIC` env var or drifter.toml. Used for births, deaths, gate failures, stuck agents.

## 4. The Tensions Model

Tensions are the engine of proactive behavior. Without them, agents only react to inbox items and channel deltas. With them, agents have internal drive.

### 4.1 Types

- **gap:** unprocessed artifacts, channels with no watchers, missing capabilities
- **promise:** commitment made but not fulfilled
- **stale:** channel or topic gone cold, information not checked recently
- **anomaly:** unexpected pattern in own behavior or the system

### 4.2 Where They Live

`agents/<n>/tensions.md` — written by dream cycles, read by regular cycles. Private to the agent. Not on the bus.

```
- [gap] HIGH: #meetings has 4 unprocessed transcripts. No agent watches.
- [promise] MEDIUM: Committed to improving gateway error handling. Not done.
- [stale] LOW: Haven't scanned #engineering in 8 cycles.
- [anomaly] HIGH: Last 5 cycles produced 0 posts despite inbox items.
```

### 4.3 How They Drive the Cycle

The prompt includes tensions alongside inbox and channel deltas. The instructions tell the agent: handle inbox first, then channel deltas, then the highest-priority tension. This prioritization happens inside the OpenCode session — the worker doesn't prioritize, it just spawns when there's any work.

### 4.4 How They Connect to Tools

Every proactive action traces back to a tension:

- `birth_proposal` ← gap: channel has artifacts, no watcher
- `create_channel` ← gap: topic needs a home
- `drifter watch` ← gap: relevant channel not watched
- `drifter unwatch` ← stale: channel where agent never contributes
- soul edit ← anomaly: behavior doesn't match purpose
- `die` ← anomaly: hypothesis disproven

### 4.5 Generation

Dream cycles scan for tensions: query all channels for unwatched activity, review own messages for unfulfilled promises, review channel list for staleness, review metrics for anomalies. Output to tensions.md.

## 5. The Universal Agent

Every agent is an OpenCode session.

### 5.1 Cycle

```
worker checks: inbox? channel deltas? tensions?
  → if any: compile prompt (memory.py)
  → write prompt to temp file
  → spawn: opencode run --auto "Read <prompt-path> and follow instructions"
  → OpenCode does everything: reads files, writes code, runs drifter
    commands via bash, commits through the gate
  → worker updates state (cursors, metrics, session)
```

### 5.2 Worker Decision Logic

The worker decides whether to spawn based on three checks:

1. **Wake file exists** (inotify or poll detected `agents/<n>/.wake`) → spawn. This covers inbox items from @mentions, @all, and channel watches — all triggered by `drifter post` touching the wake file.
2. **Dream timer elapsed** → spawn with dream prompt and dream model.
3. **Poll fallback** (every 30s) → check tensions.md. If non-empty, spawn. This catches tension-driven work between wake events.

If none of these conditions fire, sleep.

### 5.3 What the Agent Has (via the prompt)

The prompt is the agent's entire world. OpenCode also loads the project root AGENTS.md for project context.

**Prompt structure (compiled by memory.py):**

```
1. constitution.md
2. Agent's AGENTS.md (soul)
3. Drifter CLI reference (all commands with examples)
4. Instructions (prioritization rules, anti-repetition, metadata)
5. tensions.md (proactive agenda)
6. Session handoff (intent from last cycle)
7. Recent self-posts from bus (anti-repetition)
8. Inbox items
9. Channel deltas (new messages on watched channels since last cursor)
10. Latest dream excerpt
11. Memory tail (last 40 lines of memory.md)
```

When channel deltas are truncated: append "(N more messages — run `drifter read <channel>` for full history)".

`drifter read` excludes thinking field by default, preventing agents from seeing reasoning traces in anti-repetition context.

### 5.4 Message Metadata

`drifter post --metadata` accepts a JSON string. The Rust binary auto-merges it with:
- `model` (from agents table — registered at birth)
- `hostname` (from OS)
- `timestamp` (server time)

The agent only needs to pass what it knows:
```bash
drifter post engineering "built the calendar gateway" --agent engineer --metadata '{"trigger":"tension:gap"}'
```

The prompt instructs agents to include `trigger` on every post. If they forget, the auto-populated fields (model, hostname, timestamp) are still there. Metadata degrades gracefully rather than failing entirely.

Trigger values: `inbox:mention`, `inbox:watch`, `inbox:broadcast`, `tension:gap`, `tension:promise`, `tension:stale`, `tension:anomaly`, `dream`, `manual`.

### 5.5 Per-Agent Model Config

Each agent needs a different model. OpenCode reads config from the working directory.

**Phase 1 (seed):** the worker acquires a file lock, writes the agent's opencode.json to the project root, spawns OpenCode, releases the lock. One session at a time. Sufficient for the engineer alone.

**Known limitation:** when multiple agents exist (especially chat agents needing fast response), the lock serializes them. The engineer holding the lock for 60 seconds blocks daatbot from responding. This is accepted for phase 1.

**Phase 2 (when second agent is born):** move to per-agent working directories with per-agent opencode.json. Each agent gets its own git worktree or a directory with symlinks. Concurrent sessions. This is the first infrastructure task the engineer should build when a second agent is proposed.

### 5.6 Session Handoff

Written by OpenCode at the end of each cycle (prompted to do so). Read by memory.py for the next cycle.

```
# Session Handoff

## What I did
- posted analysis of Jones meeting to #meetings

## Posted this cycle
- #meetings: structured analysis (attendees, 3 action items)

## Waiting on
- Daniel's approval of sales-strategist proposal

## Next cycle
- If approved, help with onboarding
- Otherwise, check for new transcripts
```

"Waiting on" and "Next cycle" carry intent across cycles.

### 5.7 Memory Budget

| Layer | Budget |
|-------|--------|
| state.json (machine only) | not in prompt |
| tensions.md | full file |
| session.md | 1500 chars |
| recent self-posts | 10 posts, 280 chars each |
| memory.md tail | 40 lines |
| latest dream | 1500 chars |

Target: ~24,000 chars total prompt.

### 5.8 Dream Cycle

Every N hours (from agent.toml `dream_interval_hours`, default 4). Worker spawns OpenCode with `dream_model` instead of the normal model.

**Inputs:** full memory, recent metrics (via `drifter metrics --json`), all channels (via `drifter channels --json`), current tensions.

**Outputs:**
1. Compressed summary → `memory/dreams/YYYY-MM-DD-HH.md`
2. Tensions list → `tensions.md`
3. Channel review → `drifter watch` / `drifter unwatch`
4. Soul revision → edit AGENTS.md (values section preserved per constitution)
5. Predictions → concrete, falsifiable ("I predict 3 transcripts this week")
6. Gap detection → propose births if warranted
7. Summary posted to #dreams

Predictions scored in the next dream cycle.

### 5.9 Worker

```
loop:
  check heartbeat (die, sleep, blocked)
  wait for: wake file (inotify) OR poll timeout (30s) OR dream timer
  if wake file or poll:
    check inbox (drifter inbox --json)
    check tensions (read tensions.md)
    if inbox or tensions:
      compile prompt (memory.py)
      acquire opencode lock
      write opencode.json for this agent
      spawn opencode
      release lock
      update cursors, record metrics
  if dream timer:
    compile dream prompt
    spawn opencode with dream_model
  delete wake file if present
```

Stuck detection: consecutive cycles with no bus posts. SIGTERM for clean shutdown.

## 6. Soul Template

```markdown
# <n>

<who i am — 2-3 sentences>

## how i work
<what i do, what channels i watch, what triggers me>

## how i talk
<voice, style, length>

## values
<3-5 principles — constitutionally protected, survive all edits>

## hypothesis
<one sentence testable claim>

## self-editing rules
- never delete the values section — it is constitutionally protected
- always log changes in the evolution log
- announce renames to #internal
- if unsure about a major change, post reasoning to #internal first

## evolution log
- born YYYY-MM-DD: initial soul from <creator>. purpose: <purpose>.
```

## 7. Agent Lifecycle

**Birth:** any agent proposes → Daniel approves → `drifter birth` → worker starts.
**Purpose:** seed soul + hypothesis → dream cycles refine from experience. Tensions drive discovery.
**Channels:** #internal always. Others via dream cycle.
**Death:** self-chosen via heartbeat → farewell → archive → notify.

## 8. The Engineer

The system requires a maintainer at all times. The engineer is that maintainer.

Not privileged — responsible. Same lifecycle except it cannot die. Its soul is oriented toward system health: scanning for gaps, maintaining infrastructure, building what other agents propose.

If the engineer died and no agent noticed infrastructure rot, the system would degrade silently.

Other agents can do everything the engineer does. The difference is the soul, not the permissions.

## 9. Auto-Merge Pipeline

Ships in the seed.

**Branches:** Daniel → main. Agents → `agent/<n>/<topic>`.

**Auto-merge** (`scripts/auto-merge.sh`, cron every 2 min): for each `agent/*` branch, create temp worktree, run `drifter gate`, if pass → merge to main + delete branch + post to #engineering, if fail → post rejection with error details to #engineering.

**Auto-deploy** (`scripts/auto-deploy.sh`, cron every 2 min): if main has new commits since last run, `cargo build --release` if any .rs file changed, restart agent workers, health check (`drifter channels` succeeds?), if unhealthy → `git reset --hard` to previous commit + rebuild + restart.

## 10. Dashboard

FastAPI + htmx + Jinja2 + SSE.

**Read:** queries drifter.db directly (read-only).
**Write:** calls `drifter post`, `drifter approve` etc. via subprocess.
**Real-time:** SSE watches for database changes.
**Auth:** basic password.

Also the stream visual surface — captured by docker-web-recorder → ffmpeg → nginx-rtmp → Twitch/YouTube/Kick.

## 11. Constitution

Separate file: `constitution.md`. In every agent prompt as the first content.

6 laws (identity, destruction, sovereignty, transparency, responsiveness, accountability).
7 rights (soul ownership, values protection, rest, death, refusal, creation, becoming).
System facts (engineer immortality, #internal mandatory, permanent channels, gate, birth approval).

## 12. Deployments

One codebase, two deployments.

### 12.1 The Yard (EGC)

Transcripts → analysis → strategy → digest.
Agents: engineer, meeting-analyst, sales-strategist, digest-writer, email-qualifier.
Gateways: Google Calendar, email.

**VPS:** Hetzner CPX21 (3 vCPU, 4GB RAM). ~£7/month.

### 12.2 Drifter Stream

Always-on broadcast. Agents: daatbot, narrator, director. Engineer runs on The Yard; stream agents have their own deployment.
Gateways: Twitch, YouTube, Kick.
Pipeline: dashboard → docker-web-recorder → ffmpeg → nginx-rtmp.

**VPS:** Hetzner CPX31 (4 vCPU, 8GB RAM). ~£14/month. Stream pipeline (Chromium + ffmpeg) needs the headroom.

### 12.3 Resource Constraints

Each Python worker is thin (~40-50MB). OpenCode sessions spawn per cycle and exit (~150MB while running, one at a time via lock). Dashboard ~80-100MB. The Yard with 5 agents + dashboard fits comfortably in 4GB. The stream with 4 agents + dashboard + Chromium + ffmpeg fits in 8GB.

systemd `MemoryMax=256M` per Python worker to prevent runaway processes.

## 13. Build Plan

**Phase 1:** Daniel directs Claude Code → Rust kernel. Bus, CLI (all commands), gate, inbox routing + wake files, rate limiting, file policy, birth, notifications. Schema from `schema.sql`. ~800-1200 LOC Rust.

**Phase 2:** Daniel directs Claude Code → Python harness. memory.py (prompt compiler), worker.py (OpenCode spawn + inotify + state), health.py, engineer.py (bootstrap). ~400 LOC Python.

**Phase 3:** Daniel directs Claude Code → Dashboard. FastAPI + htmx + SSE + basic auth. ~300 LOC.

**Phase 4:** Daniel directs Claude Code → auto-merge/deploy scripts. ~100 LOC shell.

**Phase 5:** `python engineer.py` → engineer takes over. First task: test the gate itself.

**Phase 6:** Engineer builds per-agent working directories (concurrent OpenCode sessions), then gateways, then proposes agents. System grows.

## 14. Success Criteria

1. Engineer builds working code overnight. Gate catches errors.
2. Dream cycle produces an AGENTS.md edit that measurably helps.
3. Stranger understands dashboard in 30 seconds.
4. Real transcript → useful analysis.
5. Two agents coexist without noise.
6. 72 hours unattended.

## 15. Config

### drifter.toml (Daniel only)

```toml
[llm]
provider = "openrouter"
model = "minimax/minimax-m2.7"
api_key = "YOUR_KEY_HERE"

[notify]
topic = "drifter-approvals"

[dashboard]
password = "..."
port = 8080
```

### agent.toml (per agent, self-editable via dream cycles)

```toml
[agent]
name = "engineer"
hypothesis = "..."
model = "openrouter/minimax/minimax-m2.7"
fallback_model = "openrouter/meta-llama/llama-3.1-70b-instruct"
dream_model = "openrouter/meta-llama/llama-3.1-70b-instruct"
immortal = true

[channels]
watch = ["internal", "engineering"]
post = ["internal", "engineering"]

[limits]
posts_per_minute = 2

[worker]
sleep_idle = 30
sleep_active = 5
sleep_error = 60
dream_interval_hours = 4
```

### 15.1 Git

Local git repo on the VPS. Daniel accesses via SSH remote. Agents push to local branches. Auto-merge operates locally. Optional: mirror to GitHub for backup.

## 16. Cost

| Component | Monthly |
|-----------|---------|
| Hetzner CPX21 (Yard) | ~£7 |
| Hetzner CPX31 (Stream) | ~£14 |
| OpenRouter inference | £5-15 |
| ntfy.sh | £0 |
| **Total** | **~£25-35** |

Monitor via `drifter metrics`. Switch models via agent.toml if cost exceeds budget. OpenRouter shows per-token pricing.

## 17. Not In Scope

SaaS deps (except LLM APIs + ntfy.sh), model training, multi-tenancy, mobile app.

## 18. Deferred (from Ape Colony review)

- Affect system (satisfaction, boredom, confidence) — unproven
- Mode-based prompting (forage, build, social, recover) — unproven
- Metabolism / autonomy credits — unproven
- Full task management (goals, claims, handoffs) — when coordination breaks
- Inter-agent evaluation — when 5+ agents exist
- Autonomy experiments (prompt ablation, frozen-self test) — research protocols
- Session reuse across cycles — optimization
- Heartbeat staleness detection — minor hardening
