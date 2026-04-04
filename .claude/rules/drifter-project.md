# Drifter Project Rules

## OpenCode CLI invocation

The harness spawns OpenCode via `opencode run --model <model> "message"`.

- There is NO `--auto` flag. The PRD originally referenced `--auto` but it does not exist.
- Pass the model via `--model`, not via a temporary `opencode.json` config file.
- The worker writes the prompt to a temp file (`drifter-prompt-*.md`) and tells OpenCode to read it.
