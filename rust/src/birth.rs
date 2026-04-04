use anyhow::{Context, Result};
use sqlx::SqlitePool;
use std::path::Path;
use std::process::Command;

/// Create a new agent: directory structure, worktree, DB registration, announcements.
pub async fn birth(
    pool: &SqlitePool,
    name: &str,
    soul: &str,
    model: &str,
    immortal: bool,
    project_root: &Path,
) -> Result<()> {
    let agent_dir = project_root.join("agents").join(name);

    // Create directory structure
    std::fs::create_dir_all(agent_dir.join("memory").join("dreams"))?;

    // Write soul
    std::fs::write(agent_dir.join("AGENTS.md"), soul)?;

    // Write agent.toml
    let agent_toml = format!(
        "[agent]\nname = \"{}\"\nmodel = \"{}\"\nposts_per_minute = 2\ndream_interval_hours = 4\n",
        name, model
    );
    std::fs::write(agent_dir.join("agent.toml"), agent_toml)?;

    // Create empty state files
    std::fs::write(agent_dir.join("state.json"), "{}")?;
    std::fs::write(agent_dir.join("session.md"), "")?;
    std::fs::write(agent_dir.join("heartbeat.md"), "")?;
    std::fs::write(agent_dir.join("tensions.md"), "")?;

    // Register in database
    crate::bus::register_agent(pool, name, model, immortal).await?;

    // Create git worktree for this agent (only if git repo exists)
    let worktree_path = agent_dir.join("worktree");
    if is_git_repo(project_root) {
        create_worktree(project_root, &worktree_path, name)?;

        // Write per-agent opencode.json in worktree
        let opencode_config = r#"{
  "$schema": "https://opencode.ai/config.json",
  "permission": {
    "bash": "allow",
    "edit": "allow",
    "read": "allow",
    "external_directory": {
      "/tmp/**": "allow",
      "*": "deny"
    }
  }
}"#;
        std::fs::write(worktree_path.join("opencode.json"), opencode_config)?;

        // Store working_dir in DB (relative to project_root)
        let working_dir_rel = worktree_path
            .strip_prefix(project_root)
            .context("worktree not under project_root")?
            .to_string_lossy()
            .to_string();
        crate::bus::set_agent_working_dir(pool, name, &working_dir_rel).await?;
    }

    // Watch #internal
    crate::bus::add_watcher(pool, name, "internal").await?;

    // Post birth announcement
    crate::bus::post(
        pool,
        "internal",
        &format!(
            "{} has been born. model={}, immortal={}",
            name, model, immortal
        ),
        name,
        Some("system"),
        Some("{\"trigger\":\"birth\"}"),
        project_root,
    )
    .await?;

    // Notify Daniel
    crate::notify::try_send(
        &format!("Agent born: {}", name),
        &format!("{} (model: {}) has been born", name, model),
        project_root,
    )
    .await?;

    println!("Born: {} (model: {}, immortal: {})", name, model, immortal);
    Ok(())
}

fn is_git_repo(project_root: &Path) -> bool {
    Command::new("git")
        .args(["rev-parse", "--git-dir"])
        .current_dir(project_root)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn create_worktree(project_root: &Path, worktree_path: &Path, branch_name: &str) -> Result<()> {
    if worktree_path.exists() {
        return Ok(());
    }

    let branch = format!("agent/worktree/{}", branch_name);

    // Create the branch if it doesn't exist
    let branch_check = Command::new("git")
        .args(["rev-parse", "--verify", &branch])
        .current_dir(project_root)
        .output()?;

    if !branch_check.status.success() {
        Command::new("git")
            .args(["branch", &branch])
            .current_dir(project_root)
            .output()
            .context("failed to create worktree branch")?;
    }

    // Create worktree
    let output = Command::new("git")
        .args([
            "worktree",
            "add",
            &worktree_path.to_string_lossy(),
            &branch,
        ])
        .current_dir(project_root)
        .output()
        .context("failed to create worktree")?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(anyhow::anyhow!("git worktree add failed: {}", stderr));
    }

    Ok(())
}
