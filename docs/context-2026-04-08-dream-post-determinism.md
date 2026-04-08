# Context: Dream Post Determinism

## Request

Investigate why the engineer's last successful dream-related commit on the VPS was stale despite newer dream activity, reason from first principles, and implement a fix for the dream-cycle posting failures.

## What Happened

The root symptom was that recent dream cycles on the VPS produced local dream artifacts but did not create a new post in the `dreams` channel and did not yield a new visible dream commit. Investigation showed the prompt instructed the model to post to `#dreams`. In shell commands, unquoted `#dreams` is treated as a comment, so `drifter post --agent engineer #dreams "..."` was parsed without the required positional arguments. VPS engineer logs from `2026-04-08T01:26Z` and `2026-04-08T05:36Z` showed repeated failures of that exact form.

The deeper architectural issue was that the harness treated a dream cycle as successful if the OpenCode process exited cleanly. That meant a partial dream could still advance `last_dream_at` even if it failed to produce the required system outputs. From the PRD, a dream cycle has explicit outputs: a dream artifact, updated `tensions.md`, updated session handoff, and a summary posted to `dreams`. The worker was not enforcing that contract.

## Decision

Move the required `dreams` bus post out of the model and into deterministic harness code.

The model is still responsible for writing dream artifacts, but the harness now owns the required bus side effect after verifying that required files changed. This narrows the probabilistic surface area: the LLM produces files, while the worker performs the protocol-level post.

## Implementation

1. Updated `harness/memory.py`:
- Added an explicit CLI note that channel names are bare names like `dreams` and `engineering`, and `#` is display-only.
- Changed dream instructions so the model writes a `## Bus Summary` section in the dream markdown.
- Removed the instruction telling the model to run `drifter post` for the dream summary.

2. Updated `harness/worker.py`:
- Added `DreamCycleError` for incomplete dream outputs.
- Added helpers to identify the current dream artifact path, hash files, extract the `## Bus Summary`, and post the dream summary deterministically via `run_drifter(... "post", "dreams", ...)`.
- Added dream verification that requires:
  - the current-hour dream markdown file to exist and change
  - `agents/<agent>/tensions.md` to change
  - `agents/<agent>/session.md` to change
- Changed `run_dream_cycle()` to:
  - snapshot pre-run file hashes
  - run OpenCode
  - verify outputs
  - post to `dreams` from the harness
  - only then set `last_dream_at`
- Treated `DreamCycleError` like other worker failures so partial dreams no longer count as successful cycles.

3. Added tests in `tests/test_worker.py`:
- success path: valid dream artifacts plus deterministic harness post
- failure path: partial dream output raises `DreamCycleError` and does not update state
- bus summary extraction prefers `## Bus Summary`

## Verification

Ran:

```bash
python3 -m pytest tests/test_worker.py tests/test_scheduler.py -q
python3 -m pytest tests/test_bus.py -q
```

Both passed locally.

## Excluded Changes

The session also included unrelated local Codex/Claude environment setup:
- `.gitignore`
- `bin/codex-local`

Those were intentionally excluded from the save commit.
