# Context: Auto-Merge And Auto-Deploy Scripts

Date: 2026-04-04

## Summary

Built the phase 4 shell automation from PRD section 9 in `scripts/common.sh`, `scripts/auto-merge.sh`, and `scripts/auto-deploy.sh`.

The key constraint was the real `drifter gate` interface in the Rust kernel: it validates uncommitted changes in the current worktree by diffing against `HEAD`. Because of that, the merge automation could not simply check out an `agent/*` branch and run the gate on committed branch history. It had to create a temporary worktree from `main`, apply the branch with `git merge --squash --no-commit`, and then run `drifter gate` against that synthetic uncommitted candidate state.

## Decisions

`auto-merge.sh`:

- Enumerates local `agent/*` branches.
- Uses a lock file under `.drifter/` to avoid concurrent runs.
- For each branch, creates a temp gate worktree from `main`.
- Applies the branch as uncommitted changes with `git merge --squash --no-commit`.
- Runs the gate via `cargo run --manifest-path rust/Cargo.toml -- gate` inside that worktree so project-root resolution still works.
- On success, creates a second temp worktree from the same `main` base, performs a normal merge commit there, and advances `refs/heads/main` with `git update-ref`.
- Deletes the merged agent branch and posts the outcome to `#engineering`.
- On merge conflict or gate failure, posts a rejection to `#engineering` with trimmed command output.

`auto-deploy.sh`:

- Uses a lock file under `.drifter/`.
- Requires the deploy checkout to be on `main` and completely clean before doing anything, because rollback uses `git reset --hard`.
- Stores the last successfully deployed `main` commit in `.drifter/last-deployed-main`.
- Rebuilds Rust when no release binary exists yet, when there is no previous deploy record, or when the deployed commit range touches Rust source, Cargo files, or migrations.
- Restarts workers directly by discovering `agents/*/agent.toml` and launching `python3 -m harness.worker --agent <name>` under `nohup`, with PID files and logs stored in `.drifter/`.
- Runs the deploy health check by calling `drifter channels`.
- If deployment fails, resets the repo to the previously deployed commit, rebuilds, restarts workers, and reruns the same health check.

## Limits

- There is no checked-in supervisor or service-manager contract yet, so worker lifecycle is owned directly by the shell scripts.
- The merge script was only exercised on the no-branch path in this session because the repo had no local `agent/*` branches.
- The deploy script was not executed in this workspace because the current checkout was not a clean deployment `main`.

## Excluded Dirty Files

These files were intentionally left out of the save commit:

- `agents/engineer/heartbeat.md`
- `agents/engineer/tensions.md`
- `.drifter/auto-merge.lock`
