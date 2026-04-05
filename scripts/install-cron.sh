#!/usr/bin/env bash
# Install cron jobs for auto-merge and auto-deploy.
# Run as root: bash scripts/install-cron.sh
# Idempotent — safe to run multiple times.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
CRON_USER="${DRIFTER_USER:-drifter-agent}"

SCHEDULER_JOB="*/2 * * * * cd $REPO_ROOT && python3 -m harness.scheduler --agent engineer >> $REPO_ROOT/.drifter/logs/scheduler.log 2>&1"
MERGE_JOB="*/2 * * * * cd $REPO_ROOT && bash scripts/auto-merge.sh >> $REPO_ROOT/.drifter/logs/auto-merge.log 2>&1"
DEPLOY_JOB="*/2 * * * * cd $REPO_ROOT && bash scripts/auto-deploy.sh >> $REPO_ROOT/.drifter/logs/auto-deploy.log 2>&1"

# Get existing crontab (suppress "no crontab" warning)
existing=$(crontab -u "$CRON_USER" -l 2>/dev/null || true)

changed=0
if ! echo "$existing" | grep -qF "harness.scheduler"; then
  existing="$existing"$'\n'"$SCHEDULER_JOB"
  changed=1
fi
if ! echo "$existing" | grep -qF "auto-merge.sh"; then
  existing="$existing"$'\n'"$MERGE_JOB"
  changed=1
fi
if ! echo "$existing" | grep -qF "auto-deploy.sh"; then
  existing="$existing"$'\n'"$DEPLOY_JOB"
  changed=1
fi

if [[ $changed -eq 1 ]]; then
  echo "$existing" | sed '/^$/d' | crontab -u "$CRON_USER" -
  echo "Cron jobs installed for $CRON_USER"
else
  echo "Cron jobs already installed"
fi

# Ensure log directory exists
mkdir -p "$REPO_ROOT/.drifter/logs"
chown "$CRON_USER":"$CRON_USER" "$REPO_ROOT/.drifter/logs"
