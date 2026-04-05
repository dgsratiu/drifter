#!/usr/bin/env bash
set -euo pipefail

# Cron doesn't source .bashrc — load cargo manually
# shellcheck source=/dev/null
[[ -f "$HOME/.cargo/env" ]] && source "$HOME/.cargo/env"

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

LOCK_FILE="$STATE_DIR/auto-deploy.lock"
LAST_DEPLOY_FILE="$STATE_DIR/last-deployed-main"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "auto-deploy already running"
  exit 0
fi

# Sync working tree to main — auto-merge advances refs/heads/main via update-ref
# without touching the index or working tree. Agents may also leave HEAD on their
# branch after a cycle. Force checkout to main before doing anything.
git -C "$REPO_ROOT" checkout -f main >/dev/null 2>&1

current_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)
previous_commit=""
if [[ -f "$LAST_DEPLOY_FILE" ]]; then
  previous_commit=$(<"$LAST_DEPLOY_FILE")
fi

if [[ -n "$previous_commit" && "$current_commit" == "$previous_commit" ]]; then
  log "main unchanged since last deploy"
  exit 0
fi

needs_build=0
if [[ ! -x "$REPO_ROOT/rust/target/release/drifter" ]]; then
  needs_build=1
elif [[ -z "$previous_commit" ]]; then
  needs_build=1
elif git -C "$REPO_ROOT" diff --name-only "$previous_commit..$current_commit" | grep -Eq '^rust/.*\.rs$|^rust/Cargo\.toml$|^rust/Cargo\.lock$|^rust/migrations/'; then
  needs_build=1
fi

deploy_candidate() {
  if [[ $needs_build -eq 1 ]]; then
    cargo build --release --manifest-path "$REPO_ROOT/rust/Cargo.toml"
  fi
  stop_workers
  run_drifter channels >/dev/null
}

rollback() {
  local target=$1
  [[ -n "$target" ]] || return 1
  log "rolling back to $target"
  git -C "$REPO_ROOT" reset --hard "$target"
  cargo build --release --manifest-path "$REPO_ROOT/rust/Cargo.toml"
  stop_workers
  run_drifter channels >/dev/null
}

log "deploying main at $current_commit"
if deploy_candidate; then
  printf '%s\n' "$current_commit" >"$LAST_DEPLOY_FILE"
  post_engineering "auto-deploy OK: deployed ${current_commit:0:12}"
  log "deploy healthy at $current_commit"
  exit 0
fi

if rollback "$previous_commit"; then
  run_drifter notify "Deploy ROLLBACK" \
    "Rolled back from ${current_commit:0:12} to ${previous_commit:0:12} after health check failure" >/dev/null 2>&1 || true
  log "rollback healthy at $previous_commit"
else
  run_drifter notify "Deploy FAIL" \
    "Health check failed at ${current_commit:0:12}, rollback to ${previous_commit:0:12} also failed" >/dev/null 2>&1 || true
  log "rollback failed"
  exit 1
fi
