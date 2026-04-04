use anyhow::Result;
use sqlx::SqlitePool;
use std::collections::HashMap;
use std::path::Path;

/// Route a newly posted message to inboxes and touch wake files.
///
/// Priority: mention > broadcast > watch. Each agent gets at most one
/// inbox entry per message. The poster never inboxes themselves.
pub async fn route(
    pool: &SqlitePool,
    message_id: &str,
    channel_id: &str,
    channel_name: &str,
    poster: &str,
    content: &str,
    project_root: &Path,
) -> Result<()> {
    let mut targets: HashMap<String, &str> = HashMap::new();

    // 1. Channel watchers (lowest priority)
    let watchers = crate::bus::get_watchers(pool, channel_name).await?;
    for w in watchers {
        if w != poster {
            targets.insert(w, "watch");
        }
    }

    // 2. @all broadcast (overwrites watch)
    if has_broadcast(content) {
        let all = crate::bus::get_all_agent_names(pool).await?;
        for a in all {
            if a != poster {
                targets.insert(a, "broadcast");
            }
        }
    }

    // 3. @mentions (highest priority, overwrites broadcast/watch)
    let mentions = find_mentions(content);
    let agents = crate::bus::get_all_agent_names(pool).await?;
    for mention in mentions {
        if agents.contains(&mention) && mention != poster {
            targets.insert(mention, "mention");
        }
    }

    // Create inbox entries and touch wake files
    for (agent, trigger) in &targets {
        sqlx::query(
            "INSERT INTO inbox (agent_name, message_id, channel_id, trigger) \
             VALUES (?, ?, ?, ?)",
        )
        .bind(agent)
        .bind(message_id)
        .bind(channel_id)
        .bind(*trigger)
        .execute(pool)
        .await?;

        touch_wake(project_root, agent);
    }

    Ok(())
}

/// Extract @mention names from message content.
/// Returns agent names without the @ prefix. Excludes @all.
fn find_mentions(content: &str) -> Vec<String> {
    content
        .split_whitespace()
        .filter_map(|word| {
            let rest = word.strip_prefix('@')?;
            let name: String = rest
                .chars()
                .take_while(|c| c.is_alphanumeric() || *c == '-' || *c == '_')
                .collect();
            if !name.is_empty() && name != "all" {
                Some(name)
            } else {
                None
            }
        })
        .collect()
}

/// Check if content contains @all as a distinct word.
fn has_broadcast(content: &str) -> bool {
    content.split_whitespace().any(|word| {
        if let Some(rest) = word.strip_prefix("@all") {
            rest.is_empty() || !rest.starts_with(|c: char| c.is_alphanumeric())
        } else {
            false
        }
    })
}

/// Touch the wake file for an agent. Silently ignores missing directories.
fn touch_wake(project_root: &Path, agent: &str) {
    let wake_path = project_root.join("agents").join(agent).join(".wake");
    if let Some(parent) = wake_path.parent() {
        if parent.exists() {
            let _ = std::fs::write(&wake_path, "");
        }
    }
}
