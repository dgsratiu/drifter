# TASKS.md

The engineer works through this list top to bottom.
First unchecked item is the next task.

## Harden

- [x] Test the gate — verify py_compile catches syntax errors, pytest failures block commits, migration safety rejects modified migrations, cargo check catches Rust errors. The gate is the foundation. Test it first.
- [ ] Test bus operations — post, read, inbox/ack cycle, watcher routing, wake file creation, proposals, rate limiting, metrics.
- [x] Build per-agent working directories — each agent needs its own opencode.json for concurrent OpenCode sessions. Without this, a second agent can't run alongside the engineer. Build this before the first birth.
- [ ] Implement dream cycle in worker.py — every N hours, compile a dream-specific prompt (full memory, metrics, all channels, current tensions), spawn OpenCode with dream_model. OpenCode writes dreams/, tensions.md, soul revisions, and posts to #dreams.

## Connect to the World (EGC)

- [ ] Build gateways/calendar.py — Google Calendar gateway. Poll for completed meetings. Fetch transcript from Drive. Post to #meetings via drifter post.
- [ ] Birth meeting-analyst — write seed soul, run drifter propose. Watches #meetings, produces structured analysis.
- [ ] Build digest system — compile 24h bus summary, post to #digest or email Daniel.
- [ ] Birth sales-strategist — watches #meetings for analyst output, produces talking points.

## Connect to the World (Stream)

- [ ] Build gateways/twitch.py — TwitchIO v3 gateway. IRC to bus bridge via drifter post/read.
- [ ] Birth daatbot — chat personality. Watches #chat.
- [ ] Birth narrator — contextualizes what's on screen.
- [ ] Build stream pipeline — docker-web-recorder + nginx-rtmp.

## Evolve

- [ ] Add YouTube Live chat gateway.
- [ ] Add Kick chat gateway.
- [ ] Add sqlite-vec for semantic memory search.
- [ ] Improve dream cycle — tension-driven proactive work, predictions, model self-evaluation.
