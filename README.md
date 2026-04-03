# Drifter

An environment where artifacts generate knowledge automatically.

Rust kernel + Python harness + OpenCode agents. Read `docs/prd.md` for the full spec.

## Build

Daniel directs Claude Code through each phase:

1. `rust/` — the kernel (bus, CLI, gate, policy)
2. `harness/` — thin Python (worker, prompt compiler)
3. `dashboard/` — FastAPI + htmx
4. `scripts/` — auto-merge + auto-deploy
5. Start the engineer agent

## Acknowledgements

Inspired by [APES](https://git.unslope.com/benji/apes).
