#!/usr/bin/env bash
set -euo pipefail

# Cron doesn't source .bashrc — load cargo manually
# shellcheck source=/dev/null
[[ -f "$HOME/.cargo/env" ]] && source "$HOME/.cargo/env"

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

LOCK_FILE="$STATE_DIR/auto-merge.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "auto-merge already running"
  exit 0
fi

# Sync with origin if remote exists (optional — VPS may be the sole repo)
if git -C "$REPO_ROOT" remote get-url origin >/dev/null 2>&1; then
  git -C "$REPO_ROOT" fetch --prune origin
  sync_main_with_origin
fi

merge_branch() {
  local branch=$1
  local base_commit temp_dir gate_tree merge_tree gate_output merge_output new_main old_main

  base_commit=$(git -C "$REPO_ROOT" rev-parse refs/heads/main)
  temp_dir=$(mktemp -d)
  gate_tree="$temp_dir/gate"
  merge_tree="$temp_dir/merge"

  cleanup() {
    git -C "$REPO_ROOT" worktree remove --force "$gate_tree" >/dev/null 2>&1 || true
    git -C "$REPO_ROOT" worktree remove --force "$merge_tree" >/dev/null 2>&1 || true
    rm -rf "$temp_dir"
  }
  trap cleanup RETURN

  # Gate worktree: squash-merge to produce uncommitted changes for drifter gate
  git -C "$REPO_ROOT" worktree add --detach "$gate_tree" "$base_commit" >/dev/null
  if ! (
    cd "$gate_tree"
    git merge --squash --no-commit "$branch" >/dev/null 2>&1
  ); then
    gate_output=$(cd "$gate_tree" && git status --short && git diff --cached --stat || true)
    post_engineering "$(printf 'auto-merge CONFLICT %s: could not apply branch cleanly\n%s' "$branch" "$(trim_output "$gate_output")")"
    return
  fi

  # Resolve drifter binary from project root (avoids recompiling in temp worktree)
  local drifter_bin
  drifter_bin="$REPO_ROOT/rust/target/release/drifter"
  if [[ ! -x "$drifter_bin" ]]; then
    drifter_bin="$REPO_ROOT/rust/target/debug/drifter"
  fi

  export DRIFTER_BIN="$drifter_bin"
  if ! gate_output=$(cd "$gate_tree" && "$drifter_bin" gate 2>&1); then
    post_engineering "$(printf 'auto-merge REJECT %s: gate failed\n%s' "$branch" "$(trim_output "$gate_output")")"
    return
  fi

  # Merge worktree: real merge for the commit history
  git -C "$REPO_ROOT" worktree add --detach "$merge_tree" "$base_commit" >/dev/null
  if ! merge_output=$(
    cd "$merge_tree" &&
    git -c user.name="Drifter Agent" -c user.email="agent@drifter.local" checkout -B drifter-merge "$base_commit" >/dev/null 2>&1 &&
    git merge --no-ff --no-edit "$branch" 2>&1
  ); then
    post_engineering "$(printf 'auto-merge REJECT %s: merge to main failed\n%s' "$branch" "$(trim_output "$merge_output")")"
    return
  fi

  new_main=$(git -C "$merge_tree" rev-parse HEAD)
  old_main=$(git -C "$REPO_ROOT" rev-parse refs/heads/main)
  if ! git -C "$REPO_ROOT" update-ref refs/heads/main "$new_main" "$old_main"; then
    post_engineering "auto-merge SKIP $branch: main moved during merge"
    return
  fi

  # Push to origin if remote exists
  if git -C "$REPO_ROOT" remote get-url origin >/dev/null 2>&1; then
    if ! git -C "$REPO_ROOT" push origin main >/dev/null 2>&1; then
      git -C "$REPO_ROOT" update-ref refs/heads/main "$old_main" "$new_main" || true
      post_engineering "auto-merge REJECT $branch: passed gate but push to origin failed"
      return
    fi
  fi

  git -C "$REPO_ROOT" branch -D "$branch" >/dev/null 2>&1 || true
  post_engineering "auto-merge PASS: $branch merged to main"
}

mapfile -t branches < <(git -C "$REPO_ROOT" for-each-ref --format='%(refname:short)' refs/heads/agent/)

if [[ ${#branches[@]} -eq 0 ]]; then
  log "no agent branches to merge"
  exit 0
fi

for branch in "${branches[@]}"; do
  log "processing $branch"
  if git -C "$REPO_ROOT" remote get-url origin >/dev/null 2>&1; then
    sync_main_with_origin
  fi
  merge_branch "$branch"
done
