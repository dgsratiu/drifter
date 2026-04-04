#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

LOCK_FILE="$STATE_DIR/auto-merge.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "auto-merge already running"
  exit 0
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

  git -C "$REPO_ROOT" worktree add --detach "$gate_tree" "$base_commit" >/dev/null
  if ! (
    cd "$gate_tree"
    git merge --squash --no-commit "$branch" >/dev/null 2>&1
  ); then
    gate_output=$(cd "$gate_tree" && git status --short && git diff --cached --stat || true)
    post_engineering "$(printf 'auto-merge rejected %s: could not apply branch cleanly\n%s' "$branch" "$(trim_output "$gate_output")")"
    return
  fi

  if ! gate_output=$(cd "$gate_tree" && cargo run --quiet --manifest-path rust/Cargo.toml -- gate 2>&1); then
    post_engineering "$(printf 'auto-merge rejected %s: drifter gate failed\n%s' "$branch" "$(trim_output "$gate_output")")"
    return
  fi

  git -C "$REPO_ROOT" worktree add --detach "$merge_tree" "$base_commit" >/dev/null
  if ! merge_output=$(
    cd "$merge_tree" &&
    git checkout -b drifter-merge "$base_commit" >/dev/null 2>&1 &&
    git merge --no-ff --no-edit "$branch" 2>&1
  ); then
    post_engineering "$(printf 'auto-merge rejected %s: merge to main failed\n%s' "$branch" "$(trim_output "$merge_output")")"
    return
  fi

  new_main=$(git -C "$merge_tree" rev-parse HEAD)
  old_main=$(git -C "$REPO_ROOT" rev-parse refs/heads/main)
  if ! git -C "$REPO_ROOT" update-ref refs/heads/main "$new_main" "$old_main"; then
    post_engineering "auto-merge skipped $branch: main moved during merge"
    return
  fi

  git -C "$REPO_ROOT" branch -D "$branch" >/dev/null
  post_engineering "auto-merge merged $branch into main"
}

mapfile -t branches < <(git -C "$REPO_ROOT" for-each-ref --format='%(refname:short)' refs/heads/agent/)

if [[ ${#branches[@]} -eq 0 ]]; then
  log "no agent branches to merge"
  exit 0
fi

for branch in "${branches[@]}"; do
  log "processing $branch"
  merge_branch "$branch"
done
