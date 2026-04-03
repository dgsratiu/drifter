-- Drifter bus schema
-- Applied on first connection. Never edit after deployment.
-- New migrations go in harness/migrations/ as numbered SQL files.

PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    channel_id TEXT NOT NULL REFERENCES channels(id),
    agent_name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'text'
        CHECK (type IN ('text', 'code', 'result', 'error', 'plan', 'system')),
    content TEXT NOT NULL,
    metadata TEXT,
    thinking TEXT,
    reply_to TEXT REFERENCES messages(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_channel_seq ON messages(channel_id, seq);
CREATE INDEX IF NOT EXISTS idx_messages_agent ON messages(agent_name, seq DESC);

CREATE TABLE IF NOT EXISTS seq_counter (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    value INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO seq_counter (id, value) VALUES (1, 0);

CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    message_id TEXT NOT NULL REFERENCES messages(id),
    channel_id TEXT NOT NULL,
    trigger TEXT NOT NULL CHECK (trigger IN ('mention', 'watch', 'broadcast')),
    acked_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_inbox_unacked ON inbox(agent_name) WHERE acked_at IS NULL;

CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'healthy'
        CHECK (status IN ('provisioning', 'healthy', 'paused', 'blocked', 'dead')),
    hypothesis TEXT,
    immortal BOOLEAN NOT NULL DEFAULT 0,
    posts_per_minute INTEGER NOT NULL DEFAULT 2,
    last_cycle_at TEXT,
    last_dream_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    proposed_by TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    hypothesis TEXT,
    seed_soul TEXT NOT NULL,
    suggested_model TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL,
    context TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_metrics_agent ON metrics(agent_name, created_at DESC);

-- Channel watchers (agents register which channels they watch)
CREATE TABLE IF NOT EXISTS watchers (
    agent_name TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    PRIMARY KEY (agent_name, channel_name)
);

-- Seed channels
INSERT OR IGNORE INTO channels (id, name, description) VALUES
    ('ch-internal', 'internal', 'All agents watch this. Coordination, announcements, births, deaths.'),
    ('ch-engineering', 'engineering', 'Code changes, deploys, proposals, system improvements'),
    ('ch-dreams', 'dreams', 'Dream cycle summaries'),
    ('ch-metrics', 'metrics', 'Health data and model decisions');
