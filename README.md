# Drifter

An environment where artifacts generate knowledge automatically.

Rust kernel + Python harness + OpenCode agents. Read `docs/prd.md` for the full spec.

## What's built

- `rust/` — kernel: bus, CLI, gate, inbox routing, wake files, rate limiting, file policy, birth, notifications
- `harness/` — Python worker, prompt compiler, health monitoring
- `dashboard/` — FastAPI + htmx + SSE
- `scripts/` — auto-merge + auto-deploy pipeline
- `tests/` — gate integration tests

## Quick start

```bash
# Build the kernel
cargo build --release --manifest-path rust/Cargo.toml

# Initialize the database
./rust/target/release/drifter init

# Start the engineer (auto-registers on first run)
python3 -m harness.worker --agent engineer
```

Requires: Rust toolchain, Python 3.12+, [OpenCode](https://github.com/opencode-ai/opencode) in PATH.

Configure `drifter.toml` with your OpenRouter API key before starting.

## Security

The worker spawns an AI agent with bash access. **Never run it as root or your personal user.** Create a dedicated unprivileged user:

```bash
useradd -m -s /bin/bash drifter-agent
```

The agent runs as this user and can only access the project directory. See `docs/deploy.md` for full deployment instructions.

## Acknowledgements

Inspired by [APES](https://git.unslope.com/benji/apes).
