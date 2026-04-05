# Drifter Project Rules

## OpenCode CLI invocation

The harness spawns OpenCode via `opencode run --model <model> "message"`.

- There is NO `--auto` flag. The PRD originally referenced `--auto` but it does not exist.
- Pass the model via `--model`, not via a temporary `opencode.json` config file.
- The worker writes the prompt to a temp file (`drifter-prompt-*.md`) and tells OpenCode to read it.

## OpenCode security model

OpenCode's `external_directory` config only restricts file tools (Read, Write, Edit), not bash. Bash can still access anything the user's shell can — `rm -rf ~`, `cat ~/.ssh/id_rsa`, network access via curl. Environment scrubbing (`opencode_env()` in worker.py) is the real containment boundary, not file-tool restrictions.
