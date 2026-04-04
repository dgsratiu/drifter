#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
STATE_DIR="$REPO_ROOT/.drifter"
LOG_DIR="$STATE_DIR/logs"
PID_DIR="$STATE_DIR/pids"

mkdir -p "$STATE_DIR" "$LOG_DIR" "$PID_DIR"

log() {
  printf '[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

trim_output() {
  printf '%s\n' "${1:-}" | tail -n 80
}

run_drifter() {
  if [[ -n "${DRIFTER_BIN:-}" ]]; then
    "$DRIFTER_BIN" --db "$REPO_ROOT/drifter.db" "$@"
    return
  fi

  if [[ -x "$REPO_ROOT/rust/target/release/drifter" ]]; then
    "$REPO_ROOT/rust/target/release/drifter" --db "$REPO_ROOT/drifter.db" "$@"
    return
  fi

  if [[ -x "$REPO_ROOT/rust/target/debug/drifter" ]]; then
    "$REPO_ROOT/rust/target/debug/drifter" --db "$REPO_ROOT/drifter.db" "$@"
    return
  fi

  cargo run --quiet --manifest-path "$REPO_ROOT/rust/Cargo.toml" -- --db "$REPO_ROOT/drifter.db" "$@"
}

post_engineering() {
  local message=$1
  if ! run_drifter post engineering "$message" --agent system --type system >/dev/null; then
    log "failed to post to #engineering"
  fi
}

list_agents() {
  find "$REPO_ROOT/agents" -mindepth 2 -maxdepth 2 -name agent.toml -printf '%h\n' 2>/dev/null \
    | sed "s#^$REPO_ROOT/agents/##" \
    | sort
}

stop_workers() {
  local pid_file pid
  shopt -s nullglob
  for pid_file in "$PID_DIR"/worker-*.pid; do
    pid=$(<"$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      for _ in {1..20}; do
        if ! kill -0 "$pid" 2>/dev/null; then
          break
        fi
        sleep 0.5
      done
      if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
      fi
    fi
    rm -f "$pid_file"
  done
  shopt -u nullglob
}

start_workers() {
  local agent log_file pid_file pid
  while IFS= read -r agent; do
    [[ -n "$agent" ]] || continue
    log_file="$LOG_DIR/worker-$agent.log"
    pid_file="$PID_DIR/worker-$agent.pid"
    (
      cd "$REPO_ROOT"
      nohup python3 -m harness.worker --agent "$agent" >>"$log_file" 2>&1 &
      echo $! >"$pid_file"
    )
    pid=$(<"$pid_file")
    log "started worker $agent pid=$pid"
  done < <(list_agents)
}

restart_workers() {
  stop_workers
  start_workers
}
