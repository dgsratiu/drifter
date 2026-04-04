use anyhow::{Context, Result};
use serde::Deserialize;
use std::path::Path;

#[derive(Deserialize)]
struct Config {
    notify: Option<NotifyConfig>,
}

#[derive(Deserialize)]
struct NotifyConfig {
    topic: Option<String>,
}

/// Send notification via ntfy.sh. Errors if topic not configured.
pub async fn send(title: &str, message: &str, project_root: &Path) -> Result<()> {
    let topic = get_topic(project_root)?;

    reqwest::Client::new()
        .post(format!("https://ntfy.sh/{}", topic))
        .header("Title", title)
        .body(message.to_string())
        .send()
        .await
        .context("failed to send notification")?
        .error_for_status()
        .context("ntfy.sh returned an error status")?;

    println!("Notified: {}", title);
    Ok(())
}

/// Send notification, silently skipping if topic not configured.
pub async fn try_send(title: &str, message: &str, project_root: &Path) -> Result<()> {
    let topic = match get_topic(project_root) {
        Ok(t) => t,
        Err(_) => return Ok(()),
    };

    let _ = reqwest::Client::new()
        .post(format!("https://ntfy.sh/{}", topic))
        .header("Title", title)
        .body(message.to_string())
        .send()
        .await
        .and_then(|resp| resp.error_for_status());

    Ok(())
}

fn get_topic(project_root: &Path) -> Result<String> {
    if let Ok(topic) = std::env::var("NTFY_TOPIC") {
        return Ok(topic);
    }

    let content = std::fs::read_to_string(project_root.join("drifter.toml"))
        .context("NTFY_TOPIC not set and drifter.toml not found")?;
    let config: Config = toml::from_str(&content)?;

    config
        .notify
        .and_then(|n| n.topic)
        .context("NTFY_TOPIC not set and notify.topic not found in drifter.toml")
}
