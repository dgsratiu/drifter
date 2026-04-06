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

## Message bus filtering

- In the message bus, differentiate message types (`--type system` = informational, `--type error` = actionable) so the scheduler can filter without spawning workers. A single `from_agent` field is insufficient when the same sender (e.g., "system") produces both informational (PASS, OK) and actionable (REJECT, CONFLICT) messages. The scheduler checks both `from_agent` and `msg_type` — system+system = auto-ack, system+error = wake worker.

## Auto-deploy

- Auto-deploy should only kill stale workers (`stop_workers`), never start new ones (`restart_workers`). Starting workers bypasses the scheduler's priority logic and pollutes state (`last_cycle_at`, cooldowns) that the scheduler depends on. The scheduler (cron every 2 min) handles worker lifecycle.

## Prompt construction

- When the scheduler already knows the trigger type (inbox, rejected, tensions, dream), build the worker prompt to reflect that decision — suppress irrelevant sections (e.g., channel deltas for a rejected-branch trigger) rather than relying on the model to ignore them. The trigger flows from scheduler (`--trigger`) through worker to `compile_regular_prompt(trigger=...)`.

## Testing

- `DRIFTER_BIN` in tests must be an absolute path — tests run with `tmp_path` as CWD, so relative paths resolve against the temp directory, not the project root. Use `os.path.abspath()` when constructing the default.

## Rejected branches tracking

- Auto-merge records `branch sha` pairs in `.drifter/rejected-branches` after REJECT/CONFLICT. Before processing a branch, it checks the file: same SHA = skip (no new commits from agent), different SHA = clear entry and re-process (agent pushed a fix). Entries cleaned on merge success. The scheduler reads this file as a trigger (with 10-min cooldown) so the engineer discovers rejected branches without inbox notifications.

## Architectural invariants

These sections are protected by convention, not by the gate. They compile and pass tests if changed, but break system guarantees. Do not restructure, reorder, or weaken them without Daniel's approval.

- **Priority waterfall ordering** (`scheduler.py` `main()`): inbox > rejected > tensions > dream. Reordering breaks responsiveness — human messages must always preempt.
- **Cooldown parameters** (`scheduler.py`): 10min for rejected, 4h for tensions. Tuned to prevent re-triggering on unresolvable state. Tensions also use SHA256 content-hash dedup (`last_tensions_hash` in state.json) — cooldown alone is insufficient because the dream cycle rewrites tensions.md with identical content.
- **Environment allowlist** (`worker.py` `opencode_env()`): security boundary. Adding env vars leaks credentials to the LLM subprocess.
- **Session timeout** (`worker.py` `SESSION_TIMEOUT`): 30min. Must exceed model response time, must not hold the lock so long the scheduler can never run.
- **Ack on success only** (`worker.py` `run_regular_cycle()`): acking on failure silently drops tasks. Circuit breaker handles persistent failures separately.
- **Work detection formula** (`memory.py` `compile_regular_prompt()`): `has_work` must match the scheduler's trigger logic or cycles run with no work.
- **Trigger suppression** (`memory.py` `compile_regular_prompt()`): when the scheduler sets a focused trigger, irrelevant prompt sections are suppressed. Removing this wastes tokens and confuses the model.
- **Immutable files** (`gate.rs`): constitutional protection for `constitution.md` and `drifter.toml`.
- **Agent sovereignty** (`gate.rs`): agents cannot modify other agents' directories. Branch `agent/<name>/*` can only touch `agents/<name>/`.
- **Agent migration restriction** (`gate.rs`): agents cannot create DB migrations — propose via bus instead.
- **Path antipattern check** (`gate.rs`): gateways/ and dashboard/ files cannot use `Path(__file__).resolve().parent.parent` — gate reads file contents and rejects the pattern.

When building from an inbox item or tension, prefer changes that improve the system generally over narrow fixes. Test: "If this exact request disappeared, would this change still be worthwhile?"

## Agent prompt completeness

- The CLI_REFERENCE in `memory.py` is the only capability cheat sheet agents see every cycle. If a command or workflow isn't listed there, agents don't know it exists and will improvise — e.g., creating `agents/<other>/AGENTS.md` directly instead of using `drifter propose`. Keep CLI_REFERENCE exhaustive for all agent-facing commands and multi-step flows (propose → approve → birth).

## Gate detached HEAD

- Auto-merge runs the gate in a detached worktree (squash-merged at main). `git rev-parse --abbrev-ref HEAD` returns `"HEAD"`, not the branch name. Any branch-based gate check must use the `--branch` CLI override (`drifter gate --branch <name>`), not `current_branch()`. Auto-merge.sh passes `--branch "$branch"` to the gate for this reason. If you add a new branch-dependent check to gate.rs, use the `branch` variable (resolved from override or fallback), never call `current_branch()` directly.

## Gateway path resolution

- Gateway code must not resolve `project_root` from `Path(__file__).parent.parent`. This makes tests depend on the real project's runtime state (e.g., `.drifter/*.state` files). Accept `project_root` as a function parameter or CLI argument so tests can pass `tmp_path`. The harness code (`common.py`) does this correctly — `resolve_project_root()` is explicit and overridable. Gateways should follow the same pattern. Tests that use `__file__`-resolved paths are time bombs: they pass at gate time but break after the gateway's first real run populates state files.
- **Gate-enforced**: The gate reads changed `.py` files under `gateways/` and `dashboard/` and rejects any containing `Path(__file__).resolve().parent.parent`. Acts as a ratchet — existing files only flagged when modified.
