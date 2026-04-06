# Session: trigger context + autobot comparison + gate fix

**Date:** 2026-04-06

## What happened

Three activities this session:

### 1. Autobot architecture comparison

Cloned and deeply explored github.com/crystal-autobot/autobot (Crystal-lang personal AI agent framework). Produced honest architectural comparison with Drifter. Key findings:

- **Genuinely clever**: memory consolidation (MEMORY.md + HISTORY.md with LLM summarization), log sanitization (regex credential masking at write time), config validation (`autobot doctor`), token-saving tool result truncation
- **Overengineered**: plugin system (3 builtin plugins), kernel sandbox (Drifter's agent IS the executor), multi-provider abstraction (OpenCode handles this), multi-channel chat (Drifter is not a chatbot)
- **Drifter does better**: persistent SQLite bus (survives crashes), one-shot workers (no state accumulation), quality gate (semantic checks a sandbox can't enforce), tensions model (internal drive), dream cycles (reflection-as-architecture)
- **Key insight**: Autobot's unified bus (all triggers -> one channel) is cleaner for extensibility but loses the explicit priority control Drifter needs. Drifter's priority waterfall is correct for its use case.

Full analysis saved to `docs/autobot-architecture-comparison.md` (gitignored).

### 2. Trigger context: scheduler -> worker -> prompt

Diagnosed why engineer agent ignored rejected branches despite being triggered every 10 minutes. Three compounding failures:

1. **Scheduler knew trigger but didn't tell worker** — `_run_worker()` passed no context. Worker built same prompt regardless.
2. **Priority instructions didn't mention rejected branches** — "1. inbox, 2. channel deltas, 3. tensions" was the list. Rejected branches were just a section at position 9/12.
3. **Channel deltas were a massive attention sink** — Nemotron spent entire cycles reading channel history instead of acting on 2-line rejected branch entries.

**Fix**: Scheduler passes `--trigger inbox|rejected|dream` to worker. Worker threads it to `compile_regular_prompt()`. On `trigger=rejected`: focused step-by-step instructions, channel deltas suppressed, rejected branches moved to position 5 (right after instructions). System enforcement — change what the model sees, not how politely you ask it.

### 3. Gate fix + branch cleanup

- **Gate migration check** was blocking new migrations on ALL branches, not just `agent/*`. Added `current_branch()` check — only rejects on `agent/*` branches. Humans can add migrations freely.
- **test_bus.py DRIFTER_BIN** was a relative path that broke when tests ran with tmp_path as CWD. Fixed with `os.path.abspath()`.
- **Log sanitization**: Added `_SENSITIVE_RE` regex in worker.py that scrubs API keys, tokens, and credentials before writing to log files.
- **Branch cleanup**: Deleted stale `fix-gate-test-2`, rebased `transcripts-gateway-tests` onto main, force-pushed, auto-merge gated and merged it.

## Key insights

1. **System enforcement > model compliance.** The scheduler already decided the trigger. The prompt should reflect that decision, not ask the model to re-derive it from a 12-section prompt. Weak models ignore suggestions; they can't ignore what's not there.
2. **Suppress noise, don't add instructions.** Removing channel deltas from rejected-trigger prompts is more effective than adding "IMPORTANT: fix rejected branches first!" to the instructions.
3. **Daemon vs one-shot architecture determines what transfers.** Background fibers, in-process message routing, mid-session progressive disclosure don't transfer from Autobot's daemon model to Drifter's one-shot model. Memory patterns, config validation, log sanitization do.

## Commits

- `fb8d324` — Trigger context: scheduler tells worker why it was triggered (+ log sanitization, gate fix, test fixes)

## State at session end

- All changes deployed to VPS
- `transcripts-gateway-tests` merged to main via auto-merge
- `fix-gate-test-2` deleted (stale)
- Engineer agent idle, no rejected branches, inbox empty
- Autobot repo cloned at ~/autobot (reference only)
