use anyhow::Result;
use sqlx::SqlitePool;
use std::path::Path;

/// Create a new agent: directory structure, DB registration, announcements.
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
