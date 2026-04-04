mod birth;
mod bus;
mod gate;
mod inbox;
mod notify;
mod paths;
mod policy;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use sqlx::sqlite::{SqliteConnectOptions, SqliteJournalMode, SqlitePoolOptions};
use std::path::Path;
use std::str::FromStr;
use std::time::Duration;

#[derive(Parser)]
#[command(name = "drifter", about = "The drifter kernel")]
struct Cli {
    /// Path to the SQLite database
    #[arg(long, default_value = "./drifter.db")]
    db: String,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Initialize the database and project structure
    Init,

    /// Post a message to a channel
    Post {
        channel: String,
        message: String,
        #[arg(long)]
        agent: String,
        #[arg(long = "type")]
        msg_type: Option<String>,
        #[arg(long)]
        metadata: Option<String>,
    },

    /// Read messages from a channel
    Read {
        channel: String,
        #[arg(long)]
        since: Option<i64>,
        #[arg(long, default_value = "50")]
        limit: i64,
        #[arg(long)]
        json: bool,
        #[arg(long)]
        thinking: bool,
    },

    /// Show unacked inbox entries for an agent
    Inbox {
        agent: String,
        #[arg(long)]
        json: bool,
    },

    /// Acknowledge inbox entries
    Ack {
        #[arg(required = true)]
        ids: Vec<i64>,
    },

    /// List channels
    Channels {
        #[arg(long)]
        json: bool,
    },

    /// Create a channel
    ChannelCreate {
        name: String,
        #[arg(long)]
        description: Option<String>,
    },

    /// List agents
    Agents {
        #[arg(long)]
        json: bool,
    },

    /// Show metrics for an agent
    Metrics {
        agent: String,
        #[arg(long)]
        hours: Option<i64>,
        #[arg(long)]
        json: bool,
    },

    /// List proposals
    Proposals {
        #[arg(long)]
        json: bool,
    },

    /// Propose a new agent
    Propose {
        name: String,
        #[arg(long)]
        hypothesis: String,
        #[arg(long)]
        soul_file: String,
        #[arg(long)]
        model: Option<String>,
        #[arg(long, default_value = "daniel")]
        agent: String,
    },

    /// Approve a proposal (triggers birth)
    Approve { proposal_id: String },

    /// Reject a proposal
    Reject { proposal_id: String },

    /// Create a new agent
    Birth {
        n: String,
        #[arg(long)]
        soul: String,
        #[arg(long)]
        model: String,
        #[arg(long)]
        immortal: bool,
    },

    /// Kill an agent
    Kill { n: String },

    /// Watch a channel
    Watch { agent: String, channel: String },

    /// Unwatch a channel
    Unwatch { agent: String, channel: String },

    /// Run the quality gate on uncommitted changes
    Gate,

    /// Check file access policy
    PolicyCheck { agent: String, path: String },

    /// Send a push notification via ntfy.sh
    Notify { title: String, message: String },
}

async fn connect(db_path: &str) -> Result<sqlx::SqlitePool> {
    let opts = SqliteConnectOptions::from_str(&sqlite_url(db_path))?
        .create_if_missing(true)
        .journal_mode(SqliteJournalMode::Wal)
        .busy_timeout(Duration::from_secs(5))
        .foreign_keys(true);

    let pool = SqlitePoolOptions::new()
        .max_connections(1)
        .connect_with(opts)
        .await?;

    sqlx::migrate!().run(&pool).await?;

    Ok(pool)
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    let project_root = paths::resolve_project_root()?;

    // Commands that don't need a database connection
    match &cli.command {
        Commands::Gate => return gate::run(&project_root).await,
        Commands::PolicyCheck { agent, path } => {
            return policy::check(agent, path, &project_root)
        }
        Commands::Notify { title, message } => {
            return notify::send(title, message, &project_root).await
        }
        _ => {}
    }

    let db_path = paths::resolve_db_path(&cli.db, &project_root);
    let pool = connect(path_to_str(&db_path)?).await?;

    match cli.command {
        Commands::Init => {
            std::fs::create_dir_all(project_root.join("agents"))?;
            println!("Initialized drifter database at {}", db_path.display());
        }

        Commands::Post {
            channel,
            message,
            agent,
            msg_type,
            metadata,
        } => {
            bus::post(
                &pool,
                &channel,
                &message,
                &agent,
                msg_type.as_deref(),
                metadata.as_deref(),
                &project_root,
            )
            .await?;
        }

        Commands::Read {
            channel,
            since,
            limit,
            json,
            thinking,
        } => {
            let messages = bus::read_messages(&pool, &channel, since, limit, thinking).await?;
            if json {
                println!("{}", serde_json::to_string_pretty(&messages)?);
            } else {
                for msg in &messages {
                    println!("[{}] {}: {}", msg.seq, msg.agent_name, msg.content);
                }
            }
        }

        Commands::Inbox { agent, json } => {
            let items = bus::get_inbox(&pool, &agent).await?;
            if json {
                println!("{}", serde_json::to_string_pretty(&items)?);
            } else if items.is_empty() {
                println!("No inbox items");
            } else {
                for item in &items {
                    println!(
                        "[{}] {} from {} in #{}: {}",
                        item.id, item.trigger, item.from_agent, item.channel, item.content
                    );
                }
            }
        }

        Commands::Ack { ids } => {
            bus::ack_inbox(&pool, &ids).await?;
        }

        Commands::Channels { json } => {
            let channels = bus::list_channels(&pool).await?;
            if json {
                println!("{}", serde_json::to_string_pretty(&channels)?);
            } else {
                for ch in &channels {
                    if ch.description.is_empty() {
                        println!("#{}", ch.name);
                    } else {
                        println!("#{} — {}", ch.name, ch.description);
                    }
                }
            }
        }

        Commands::ChannelCreate { name, description } => {
            bus::create_channel(&pool, &name, description.as_deref()).await?;
        }

        Commands::Agents { json } => {
            let agents = bus::list_agents(&pool).await?;
            if json {
                println!("{}", serde_json::to_string_pretty(&agents)?);
            } else {
                for ag in &agents {
                    let immortal_str = if ag.immortal { " immortal" } else { "" };
                    println!(
                        "{} ({}) model={}{}",
                        ag.name, ag.status, ag.model, immortal_str
                    );
                }
            }
        }

        Commands::Metrics { agent, hours, json } => {
            let metrics = bus::get_metrics(&pool, &agent, hours).await?;
            if json {
                println!("{}", serde_json::to_string_pretty(&metrics)?);
            } else {
                for m in &metrics {
                    let val = m.value.map_or("n/a".to_string(), |v| v.to_string());
                    println!(
                        "{}: {} = {} {}",
                        m.created_at,
                        m.metric,
                        val,
                        m.context.as_deref().unwrap_or("")
                    );
                }
            }
        }

        Commands::Proposals { json } => {
            let proposals = bus::list_proposals(&pool).await?;
            if json {
                println!("{}", serde_json::to_string_pretty(&proposals)?);
            } else {
                for p in &proposals {
                    let short_id = if p.id.len() > 8 { &p.id[..8] } else { &p.id };
                    println!(
                        "[{}] {} by {} ({}) — {}",
                        short_id,
                        p.agent_name,
                        p.proposed_by,
                        p.status,
                        p.hypothesis.as_deref().unwrap_or("")
                    );
                }
            }
        }

        Commands::Propose {
            name,
            hypothesis,
            soul_file,
            model,
            agent,
        } => {
            let soul = std::fs::read_to_string(&soul_file)
                .with_context(|| format!("failed to read soul file: {}", soul_file))?;
            let id = bus::create_proposal(
                &pool,
                &agent,
                &name,
                &hypothesis,
                &soul,
                model.as_deref(),
            )
            .await?;

            // Post to #internal
            bus::post(
                &pool,
                "internal",
                &format!("@engineer proposed {}: {}", name, hypothesis),
                &agent,
                Some("system"),
                Some("{\"trigger\":\"proposal\"}"),
                &project_root,
            )
            .await?;

            // Notify Daniel
            notify::try_send(
                &format!("Proposal: {}", name),
                &format!("{} proposed by {}: {}", name, agent, hypothesis),
                &project_root,
            )
            .await?;

            let short_id = if id.len() > 8 { &id[..8] } else { &id };
            println!("Proposal created: {}", short_id);
        }

        Commands::Approve { proposal_id } => {
            let proposal = bus::approve_proposal(&pool, &proposal_id).await?;
            println!("Approved: {}", proposal.agent_name);

            // Trigger birth
            birth::birth(
                &pool,
                &proposal.agent_name,
                &proposal.seed_soul,
                proposal
                    .suggested_model
                    .as_deref()
                    .unwrap_or("openrouter/auto"),
                false,
                &project_root,
            )
            .await?;
        }

        Commands::Reject { proposal_id } => {
            bus::reject_proposal(&pool, &proposal_id).await?;
        }

        Commands::Birth {
            n,
            soul,
            model,
            immortal,
        } => {
            let soul_content = std::fs::read_to_string(&soul)
                .with_context(|| format!("failed to read soul file: {}", soul))?;
            birth::birth(&pool, &n, &soul_content, &model, immortal, &project_root).await?;
        }

        Commands::Kill { n } => {
            bus::kill_agent(&pool, &n).await?;

            // Post death notice (as system message to bypass rate limit)
            bus::post(
                &pool,
                "internal",
                &format!("{} has died", n),
                &n,
                Some("system"),
                Some("{\"trigger\":\"death\"}"),
                &project_root,
            )
            .await?;

            // Notify Daniel
            notify::try_send(
                &format!("Agent died: {}", n),
                &format!("{} has been killed", n),
                &project_root,
            )
            .await?;

            println!("Killed: {}", n);
        }

        Commands::Watch { agent, channel } => {
            bus::add_watcher(&pool, &agent, &channel).await?;
        }

        Commands::Unwatch { agent, channel } => {
            bus::remove_watcher(&pool, &agent, &channel).await?;
        }

        // These are handled above before DB connection
        Commands::Gate | Commands::PolicyCheck { .. } | Commands::Notify { .. } => {
            unreachable!()
        }
    }

    Ok(())
}

fn path_to_str(path: &Path) -> Result<&str> {
    path.to_str()
        .with_context(|| format!("path is not valid UTF-8: {}", path.display()))
}

fn sqlite_url(db_path: &str) -> String {
    if Path::new(db_path).is_absolute() {
        format!("sqlite://{}", db_path)
    } else {
        format!("sqlite:{}", db_path)
    }
}
