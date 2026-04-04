#!/usr/bin/env bash
set -euo pipefail

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

current_branch=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
if [[ "$current_branch" != "main" ]]; then
  log "auto-deploy requires the checkout branch to be main"
  exit 1
fi

if ! git -C "$REPO_ROOT" diff --quiet || ! git -C "$REPO_ROOT" diff --cached --quiet || [[ -n $(git -C "$REPO_ROOT" ls-files --others --exclude-standard) ]]; then
  log "auto-deploy requires a clean working tree"
  exit 1
fi

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
  restart_workers
  run_drifter channels >/dev/null
}

rollback() {
  local target=$1
  [[ -n "$target" ]] || return 1
  log "rolling back to $target"
  git -C "$REPO_ROOT" reset --hard "$target"
  cargo build --release --manifest-path "$REPO_ROOT/rust/Cargo.toml"
  restart_workers
  run_drifter channels >/dev/null
}

log "deploying main at $current_commit"
if deploy_candidate; then
  printf '%s\n' "$current_commit" >"$LAST_DEPLOY_FILE"
  log "deploy healthy at $current_commit"
  exit 0
fi

if rollback "$previous_commit"; then
  log "rollback healthy at $previous_commit"
else
  log "rollback failed"
  exit 1
fi
