# Drifter Project Rules

## OpenCode CLI invocation

The harness spawns OpenCode via `opencode run --model <model> "message"`.

- There is NO `--auto` flag. The PRD originally referenced `--auto` but it does not exist.
- Pass the model via `--model`, not via a temporary `opencode.json` config file.
- The worker writes the prompt to a temp file (`drifter-prompt-*.md`) and tells OpenCode to read it.

## OpenCode security model

OpenCode's `external_directory` config only restricts file tools (Read, Write, Edit), not bash. Bash can still access anything the user's shell can — `rm -rf ~`, `cat ~/.ssh/id_rsa`, network access via curl. Environment scrubbing (`opencode_env()` in worker.py) is the real containment boundary, not file-tool restrictions.

## sqlx migrations

- `sqlx::migrate!()` embeds migration files into the binary at compile time. If the DB has a migration the binary doesn't know about (e.g., applied by a different build on a feature branch), it fatally errors: "migration X was previously applied but is missing in the resolved migrations." Use `Migrator::set_ignore_missing(true)` so binaries tolerate extra migrations from other branches. Safe for SQLite — unknown columns are simply not queried.
- The quality gate (`rust/src/gate.rs`) rejects new migration files from agent branches. Agents cannot create database migrations — they must propose schema changes via the bus. The gate uses `--diff-filter=A` + untracked file check, parallel to the existing `modified_migrations()` pattern. Only affects `agent/*` branches; humans can still add migrations directly.
