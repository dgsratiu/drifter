use anyhow::{bail, Context, Result};
use serde::Serialize;
use sqlx::{FromRow, Row, SqlitePool};
use std::path::Path;

// ── Types ──────────────────────────────────────────────────────────────────

#[derive(Debug, Serialize, FromRow)]
pub struct Channel {
    pub id: String,
    pub name: String,
    pub description: String,
    pub created_at: String,
}

#[derive(Debug, Serialize, FromRow)]
pub struct Message {
    pub id: String,
    pub seq: i64,
    pub channel_id: String,
    pub agent_name: String,
    #[sqlx(rename = "type")]
    #[serde(rename = "type")]
    pub msg_type: String,
    pub content: String,
    pub metadata: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub thinking: Option<String>,
    pub reply_to: Option<String>,
    pub created_at: String,
}

#[derive(Debug, Serialize, FromRow)]
pub struct Agent {
    pub name: String,
    pub model: String,
    pub status: String,
    pub hypothesis: Option<String>,
    pub immortal: bool,
    pub posts_per_minute: i32,
    pub last_cycle_at: Option<String>,
    pub last_dream_at: Option<String>,
    pub working_dir: Option<String>,
    pub created_at: String,
}

#[derive(Debug, Serialize, FromRow)]
pub struct InboxItem {
    pub id: i64,
    pub trigger: String,
    pub channel: String,
    pub from_agent: String,
    pub content: String,
    pub seq: i64,
    pub created_at: String,
}

#[derive(Debug, Serialize, FromRow)]
pub struct Proposal {
    pub id: String,
    pub proposed_by: String,
    pub agent_name: String,
    pub hypothesis: Option<String>,
    pub seed_soul: String,
    pub suggested_model: Option<String>,
    pub status: String,
    pub reviewed_at: Option<String>,
    pub created_at: String,
}

#[derive(Debug, Serialize, FromRow)]
pub struct Metric {
    pub id: i64,
    pub agent_name: String,
    pub cycle_id: String,
    pub metric: String,
    pub value: Option<f64>,
    pub context: Option<String>,
    pub created_at: String,
}

// ── Channel Operations ────────────────────────────────────────────────────

pub async fn ensure_channel(pool: &SqlitePool, name: &str) -> Result<String> {
    let id = format!("ch-{}", name);
    sqlx::query("INSERT OR IGNORE INTO channels (id, name) VALUES (?, ?)")
        .bind(&id)
        .bind(name)
        .execute(pool)
        .await?;
    Ok(id)
}

pub async fn create_channel(
    pool: &SqlitePool,
    name: &str,
    description: Option<&str>,
) -> Result<()> {
    let id = format!("ch-{}", name);
    sqlx::query("INSERT INTO channels (id, name, description) VALUES (?, ?, ?)")
        .bind(&id)
        .bind(name)
        .bind(description.unwrap_or(""))
        .execute(pool)
        .await
        .context("channel already exists or insert failed")?;
    println!("Created channel #{}", name);
    Ok(())
}

pub async fn list_channels(pool: &SqlitePool) -> Result<Vec<Channel>> {
    Ok(sqlx::query_as::<_, Channel>(
        "SELECT id, name, description, created_at FROM channels ORDER BY name",
    )
    .fetch_all(pool)
    .await?)
}

async fn get_channel_id(pool: &SqlitePool, name: &str) -> Result<Option<String>> {
    let row: Option<(String,)> = sqlx::query_as("SELECT id FROM channels WHERE name = ?")
        .bind(name)
        .fetch_optional(pool)
        .await?;
    Ok(row.map(|r| r.0))
}

// ── Sequence ──────────────────────────────────────────────────────────────

async fn next_seq(tx: &mut sqlx::Transaction<'_, sqlx::Sqlite>) -> Result<i64> {
    let row = sqlx::query(
        "UPDATE seq_counter SET value = value + 1 WHERE id = 1 RETURNING value",
    )
    .fetch_one(tx.as_mut())
    .await?;
    Ok(row.get("value"))
}

// ── Message Operations ────────────────────────────────────────────────────

pub async fn post(
    pool: &SqlitePool,
    channel: &str,
    content: &str,
    agent: &str,
    msg_type: Option<&str>,
    metadata: Option<&str>,
    project_root: &Path,
) -> Result<String> {
    let agent_row = get_agent(pool, agent).await?;
    let msg_type = msg_type.unwrap_or("text");

    // Rate limiting (skip for system messages and unregistered agents)
    if msg_type != "system" {
        if let Some(ref ag) = agent_row {
            let (count,): (i32,) = sqlx::query_as(
                "SELECT COUNT(*) FROM messages WHERE agent_name = ? \
                 AND created_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-60 seconds')",
            )
            .bind(agent)
            .fetch_one(pool)
            .await?;

            if count >= ag.posts_per_minute {
                bail!(
                    "rate limit exceeded: {} posts in last 60s (limit: {})",
                    count,
                    ag.posts_per_minute
                );
            }
        }
    }

    // Ensure channel exists (auto-create)
    let channel_id = ensure_channel(pool, channel).await?;

    // Merge metadata (pure computation, no DB)
    let merged_meta = merge_metadata(metadata, agent_row.as_ref())?;

    // Generate message ID
    let id = uuid::Uuid::new_v4().to_string();

    // Atomic: increment seq + insert message in one transaction
    let mut tx = pool.begin().await?;
    let seq = next_seq(&mut tx).await?;
    sqlx::query(
        "INSERT INTO messages (id, seq, channel_id, agent_name, type, content, metadata) \
         VALUES (?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(&id)
    .bind(seq)
    .bind(&channel_id)
    .bind(agent)
    .bind(msg_type)
    .bind(content)
    .bind(&merged_meta)
    .execute(tx.as_mut())
    .await?;
    tx.commit().await?;

    // Route to inboxes
    crate::inbox::route(pool, &id, &channel_id, channel, agent, content, project_root).await?;

    println!("[{}] posted to #{}", seq, channel);
    Ok(id)
}

fn merge_metadata(user_meta: Option<&str>, agent: Option<&Agent>) -> Result<String> {
    let mut meta: serde_json::Value = match user_meta {
        Some(m) => serde_json::from_str(m).context("invalid metadata JSON")?,
        None => serde_json::json!({}),
    };
    let obj = meta
        .as_object_mut()
        .context("metadata must be a JSON object")?;
    if let Some(ag) = agent {
        obj.entry("model")
            .or_insert(serde_json::json!(ag.model));
    }
    obj.entry("hostname").or_insert(serde_json::json!(
        gethostname::gethostname().to_string_lossy().to_string()
    ));
    obj.entry("timestamp")
        .or_insert(serde_json::json!(chrono::Utc::now().to_rfc3339()));
    Ok(serde_json::to_string(&meta)?)
}

pub async fn read_messages(
    pool: &SqlitePool,
    channel: &str,
    since: Option<i64>,
    limit: i64,
    include_thinking: bool,
) -> Result<Vec<Message>> {
    let channel_id = get_channel_id(pool, channel)
        .await?
        .with_context(|| format!("channel '{}' not found", channel))?;

    let mut messages: Vec<Message> = sqlx::query_as(
        "SELECT id, seq, channel_id, agent_name, type, content, metadata, \
         thinking, reply_to, created_at \
         FROM messages WHERE channel_id = ? AND seq > ? ORDER BY seq LIMIT ?",
    )
    .bind(&channel_id)
    .bind(since.unwrap_or(0))
    .bind(limit)
    .fetch_all(pool)
    .await?;

    if !include_thinking {
        for msg in &mut messages {
            msg.thinking = None;
        }
    }

    Ok(messages)
}

// ── Agent Operations ──────────────────────────────────────────────────────

pub async fn get_agent(pool: &SqlitePool, name: &str) -> Result<Option<Agent>> {
    Ok(sqlx::query_as::<_, Agent>(
        "SELECT name, model, status, hypothesis, immortal, posts_per_minute, \
         last_cycle_at, last_dream_at, working_dir, created_at FROM agents WHERE name = ?",
    )
    .bind(name)
    .fetch_optional(pool)
    .await?)
}

pub async fn list_agents(pool: &SqlitePool) -> Result<Vec<Agent>> {
    Ok(sqlx::query_as::<_, Agent>(
        "SELECT name, model, status, hypothesis, immortal, posts_per_minute, \
         last_cycle_at, last_dream_at, working_dir, created_at FROM agents ORDER BY name",
    )
    .fetch_all(pool)
    .await?)
}

pub async fn register_agent(
    pool: &SqlitePool,
    name: &str,
    model: &str,
    immortal: bool,
) -> Result<()> {
    sqlx::query("INSERT INTO agents (name, model, immortal) VALUES (?, ?, ?)")
        .bind(name)
        .bind(model)
        .bind(immortal)
        .execute(pool)
        .await?;
    Ok(())
}

pub async fn set_agent_working_dir(
    pool: &SqlitePool,
    name: &str,
    working_dir: &str,
) -> Result<()> {
    sqlx::query("UPDATE agents SET working_dir = ? WHERE name = ?")
        .bind(working_dir)
        .bind(name)
        .execute(pool)
        .await?;
    Ok(())
}

pub async fn kill_agent(pool: &SqlitePool, name: &str) -> Result<()> {
    let agent = get_agent(pool, name)
        .await?
        .with_context(|| format!("agent '{}' not found", name))?;

    if agent.immortal {
        bail!("cannot kill immortal agent '{}'", name);
    }

    sqlx::query("UPDATE agents SET status = 'dead' WHERE name = ?")
        .bind(name)
        .execute(pool)
        .await?;

    Ok(())
}

pub async fn get_all_agent_names(pool: &SqlitePool) -> Result<Vec<String>> {
    let names: Vec<(String,)> =
        sqlx::query_as("SELECT name FROM agents WHERE status != 'dead'")
            .fetch_all(pool)
            .await?;
    Ok(names.into_iter().map(|r| r.0).collect())
}

// ── Inbox Operations ──────────────────────────────────────────────────────

pub async fn get_inbox(pool: &SqlitePool, agent: &str) -> Result<Vec<InboxItem>> {
    Ok(sqlx::query_as::<_, InboxItem>(
        "SELECT i.id, i.trigger, c.name as channel, m.agent_name as from_agent, \
         m.content, m.seq, i.created_at \
         FROM inbox i \
         JOIN messages m ON i.message_id = m.id \
         JOIN channels c ON i.channel_id = c.id \
         WHERE i.agent_name = ? AND i.acked_at IS NULL \
         ORDER BY i.id",
    )
    .bind(agent)
    .fetch_all(pool)
    .await?)
}

pub async fn ack_inbox(pool: &SqlitePool, ids: &[i64]) -> Result<()> {
    for id in ids {
        sqlx::query(
            "UPDATE inbox SET acked_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
        )
        .bind(id)
        .execute(pool)
        .await?;
    }
    println!("Acked {} inbox entries", ids.len());
    Ok(())
}

// ── Proposal Operations ───────────────────────────────────────────────────

pub async fn list_proposals(pool: &SqlitePool) -> Result<Vec<Proposal>> {
    Ok(sqlx::query_as::<_, Proposal>(
        "SELECT id, proposed_by, agent_name, hypothesis, seed_soul, \
         suggested_model, status, reviewed_at, created_at \
         FROM proposals ORDER BY created_at DESC",
    )
    .fetch_all(pool)
    .await?)
}

pub async fn create_proposal(
    pool: &SqlitePool,
    proposed_by: &str,
    agent_name: &str,
    hypothesis: &str,
    seed_soul: &str,
    model: Option<&str>,
) -> Result<String> {
    let id = uuid::Uuid::new_v4().to_string();
    sqlx::query(
        "INSERT INTO proposals (id, proposed_by, agent_name, hypothesis, seed_soul, suggested_model) \
         VALUES (?, ?, ?, ?, ?, ?)",
    )
    .bind(&id)
    .bind(proposed_by)
    .bind(agent_name)
    .bind(hypothesis)
    .bind(seed_soul)
    .bind(model)
    .execute(pool)
    .await?;
    Ok(id)
}

pub async fn approve_proposal(pool: &SqlitePool, id: &str) -> Result<Proposal> {
    let result = sqlx::query(
        "UPDATE proposals SET status = 'approved', \
         reviewed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') \
         WHERE id = ? AND status = 'pending'",
    )
    .bind(id)
    .execute(pool)
    .await?;

    if result.rows_affected() == 0 {
        bail!("proposal {} not found or not pending", id);
    }

    let proposal = sqlx::query_as::<_, Proposal>(
        "SELECT id, proposed_by, agent_name, hypothesis, seed_soul, \
         suggested_model, status, reviewed_at, created_at \
         FROM proposals WHERE id = ?",
    )
    .bind(id)
    .fetch_one(pool)
    .await?;

    Ok(proposal)
}

pub async fn reject_proposal(pool: &SqlitePool, id: &str) -> Result<()> {
    let result = sqlx::query(
        "UPDATE proposals SET status = 'rejected', \
         reviewed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') \
         WHERE id = ? AND status = 'pending'",
    )
    .bind(id)
    .execute(pool)
    .await?;

    if result.rows_affected() == 0 {
        bail!("proposal {} not found or not pending", id);
    }

    println!("Rejected proposal {}", id);
    Ok(())
}

// ── Metrics ───────────────────────────────────────────────────────────────

pub async fn get_metrics(
    pool: &SqlitePool,
    agent: &str,
    hours: Option<i64>,
) -> Result<Vec<Metric>> {
    let h = hours.unwrap_or(24);
    Ok(sqlx::query_as::<_, Metric>(
        "SELECT id, agent_name, cycle_id, metric, value, context, created_at \
         FROM metrics WHERE agent_name = ? \
         AND created_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-' || ? || ' hours') \
         ORDER BY created_at DESC",
    )
    .bind(agent)
    .bind(h)
    .fetch_all(pool)
    .await?)
}

// ── Watchers ──────────────────────────────────────────────────────────────

pub async fn add_watcher(pool: &SqlitePool, agent: &str, channel: &str) -> Result<()> {
    sqlx::query("INSERT OR IGNORE INTO watchers (agent_name, channel_name) VALUES (?, ?)")
        .bind(agent)
        .bind(channel)
        .execute(pool)
        .await?;
    println!("{} now watching #{}", agent, channel);
    Ok(())
}

pub async fn remove_watcher(pool: &SqlitePool, agent: &str, channel: &str) -> Result<()> {
    sqlx::query("DELETE FROM watchers WHERE agent_name = ? AND channel_name = ?")
        .bind(agent)
        .bind(channel)
        .execute(pool)
        .await?;
    println!("{} unwatched #{}", agent, channel);
    Ok(())
}

pub async fn get_watchers(pool: &SqlitePool, channel: &str) -> Result<Vec<String>> {
    let watchers: Vec<(String,)> =
        sqlx::query_as("SELECT agent_name FROM watchers WHERE channel_name = ?")
            .bind(channel)
            .fetch_all(pool)
            .await?;
    Ok(watchers.into_iter().map(|r| r.0).collect())
}
